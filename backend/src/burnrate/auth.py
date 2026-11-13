"""Dashboard login: user accounts, password hashing and cookie sessions.

Access rules for `/api/*` (the ingest API under `/v1` uses ingest keys and is unaffected):
- With no users and no `BURNRATE_DASHBOARD_TOKEN`, the dashboard is open (local demo).
- Once any user exists (even a disabled one), every request needs a valid session cookie or the dashboard token.
- `BURNRATE_DASHBOARD_TOKEN` keeps working as a bearer token for scripts and automation.

Cookie-authenticated writes must carry `X-Requested-With: burnrate`. Browsers can't add that header
cross-origin without passing CORS, which blocks cross-site request forgery.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session, with_loader_criteria

from burnrate.config import get_settings
from burnrate.db import get_session
from burnrate.models import UsageEvent, User, UserInvite, UserSession

__all__ = ["User", "UserInvite", "UserSession"]

COOKIE_NAME = "burnrate_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "burnrate"
MIN_PASSWORD_LENGTH = 10
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# scrypt cost: ~16 MiB and ~50 ms per hash. Parameters are stored with each hash so they can be raised later.
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False  # invited, password not set yet
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                                   dklen=len(digest) // 2)
    except ValueError:
        return False
    return hmac.compare_digest(candidate.hex(), digest)


# Verified against when the email is unknown, so a miss takes as long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _aware(ts: datetime | None) -> datetime | None:
    return ts.replace(tzinfo=UTC) if ts is not None and ts.tzinfo is None else ts


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ---- accounts --------------------------------------------------------------------------------------------
def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


def clean_teams(teams: list[str] | None) -> list[str] | None:
    if teams is None:
        return None
    cleaned = sorted({t.strip() for t in teams if t and t.strip()})
    if not cleaned:
        raise ValueError("Pick at least one team, or give access to all teams.")
    return cleaned


def create_user(session: Session, email: str, password: str | None, name: str | None = None,
                role: str = "admin", teams: list[str] | None = None) -> User:
    """Create a user. Without a password they can't sign in until they use an invite link."""
    email = normalize_email(email)
    if "@" not in email or len(email) > 254:
        raise ValueError("Enter a valid email address.")
    if role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
    if role == "admin" and teams is not None:
        raise ValueError("Admins always see every team.")
    if password is not None:
        validate_password(password)
    if session.scalar(select(User).where(User.email == email)):
        raise ValueError(f"A user with email {email} already exists.")
    user = User(email=email, name=(name or "").strip() or None, role=role, teams=clean_teams(teams),
                password_hash=hash_password(password) if password is not None else "")
    session.add(user)
    session.commit()
    return user


def active_admins(session: Session) -> int:
    return session.query(User).filter(User.role == "admin", User.disabled_at.is_(None), User.teams.is_(None)).count()


INVITE_DAYS = 7


def issue_invite(session: Session, user: User) -> tuple[str, datetime]:
    """A one-time link token to set a password. Replaces any earlier unused link for this user."""
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=INVITE_DAYS)
    session.execute(delete(UserInvite).where(UserInvite.user_id == user.id, UserInvite.used_at.is_(None)))
    session.add(UserInvite(user_id=user.id, token_hash=_hash_token(raw), expires_at=expires))
    session.commit()
    return raw, expires


def invite_for(session: Session, raw: str | None) -> UserInvite | None:
    if not raw:
        return None
    inv = session.scalar(select(UserInvite).where(UserInvite.token_hash == _hash_token(raw)))
    if inv is None or inv.used_at is not None or _aware(inv.expires_at) <= datetime.now(UTC):
        return None
    user = session.get(User, inv.user_id)
    return inv if user is not None and user.disabled_at is None else None


def accept_invite(session: Session, raw: str, password: str) -> User | None:
    inv = invite_for(session, raw)
    if inv is None:
        return None
    validate_password(password)
    user = session.get(User, inv.user_id)
    inv.used_at = datetime.now(UTC)
    set_password(session, user, password)  # also ends any other sessions
    return user


def set_password(session: Session, user: User, password: str) -> None:
    """Change a password and sign the user out everywhere."""
    validate_password(password)
    user.password_hash = hash_password(password)
    session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    session.commit()


def set_disabled(session: Session, user: User, disabled: bool) -> None:
    user.disabled_at = datetime.now(UTC) if disabled else None
    if disabled:
        session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    session.commit()


def find_user(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == normalize_email(email)))


def login_required(session: Session) -> bool:
    # Any user, even a disabled one: disabling everyone must lock the dashboard, never reopen it.
    return session.scalar(select(User.id).limit(1)) is not None


# ---- sessions --------------------------------------------------------------------------------------------
def authenticate(session: Session, email: str, password: str) -> User | None:
    user = find_user(session, email)
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not ok or user.disabled_at is not None:
        return None
    return user


def start_session(session: Session, user: User) -> tuple[str, datetime]:
    raw = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires = now + timedelta(days=get_settings().session_days)
    user.last_login_at = now
    # Opportunistic cleanup so the table doesn't grow without bound.
    session.execute(delete(UserSession).where(UserSession.expires_at < now))
    session.add(UserSession(user_id=user.id, token_hash=_hash_token(raw), expires_at=expires))
    session.commit()
    return raw, expires


def end_session(session: Session, raw: str | None) -> None:
    if raw:
        session.execute(delete(UserSession).where(UserSession.token_hash == _hash_token(raw)))
        session.commit()


def user_for_session(session: Session, raw: str | None) -> User | None:
    if not raw:
        return None
    row = session.scalar(select(UserSession).where(UserSession.token_hash == _hash_token(raw)))
    if row is None or _aware(row.expires_at) <= datetime.now(UTC):
        return None
    user = session.get(User, row.user_id)
    return user if user is not None and user.disabled_at is None else None


# ---- roles and team scope -------------------------------------------------------------------------------
ROLES = ("admin", "member", "viewer")
TEAM_SCOPE_KEY = "burnrate_teams"


@dataclass(frozen=True)
class Principal:
    """Who is making a dashboard request. Token and open (no users) access act as an unscoped admin."""

    user: User | None
    role: str
    teams: tuple[str, ...] | None  # None = all teams
    via: str = "session"  # "session" | "token" (BURNRATE_DASHBOARD_TOKEN) | "open" (no users exist yet)

    @property
    def scoped(self) -> bool:
        return self.teams is not None

    def to_dict(self) -> dict:
        return {"role": self.role, "teams": list(self.teams) if self.teams is not None else None}


TOKEN_ADMIN = Principal(user=None, role="admin", teams=None, via="token")
OPEN_ADMIN = Principal(user=None, role="admin", teams=None, via="open")


def principal_for(user: User) -> Principal:
    role = user.role if user.role in ROLES else "viewer"
    teams = tuple(sorted(set(user.teams))) if user.teams is not None else None
    return Principal(user=user, role=role, teams=teams)


@event.listens_for(Session, "do_orm_execute")
def _apply_team_scope(state) -> None:
    """Limit every ORM query on usage events to the caller's teams, wherever the query is built.

    Set per request by `require_dashboard_access` on that request's session. Events without a team
    are hidden from team-scoped users too.
    """
    teams = state.session.info.get(TEAM_SCOPE_KEY)
    if teams is not None and state.is_select:
        state.statement = state.statement.options(
            with_loader_criteria(UsageEvent, UsageEvent.team.in_(teams), include_aliases=True)
        )


# ---- FastAPI dependencies --------------------------------------------------------------------------------
def _token_ok(authorization: str | None) -> bool:
    expected = get_settings().dashboard_token
    if not expected or not authorization:
        return False
    supplied = authorization.removeprefix("Bearer ").strip()
    return hmac.compare_digest(supplied.encode(), expected.encode())


def require_dashboard_access(
    request: Request,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> Principal:
    """Guard for `/api/*`: who is asking, with viewers kept to reads and data limited to their teams."""
    if _token_ok(authorization):
        return TOKEN_ADMIN
    user = user_for_session(session, request.cookies.get(COOKIE_NAME))
    if user is None:
        if not get_settings().dashboard_token and not login_required(session):
            return OPEN_ADMIN  # nothing configured: open local demo
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    unsafe = request.method not in SAFE_METHODS
    if unsafe and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing X-Requested-With header")
    who = principal_for(user)
    if unsafe and who.role == "viewer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Viewers can't make changes")
    if who.scoped:
        # The endpoint gets this same session (FastAPI shares dependencies within a request).
        session.info[TEAM_SCOPE_KEY] = list(who.teams)
    return who


def require_admin(who: Principal = Depends(require_dashboard_access)) -> Principal:
    if who.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can do this")
    return who


def require_unscoped(who: Principal = Depends(require_dashboard_access)) -> Principal:
    """For views that mix every team's data (provider bills, company-wide alerts)."""
    if who.scoped:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not available to team-limited users")
    return who
