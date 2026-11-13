"""Dashboard sign-in endpoints. Open by design: they are how a browser gets a session."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from burnrate import auth
from burnrate.config import get_settings
from burnrate.db import get_session
from burnrate.services.audit import get_client_ip, record_audit

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Failed sign-ins allowed per (email, client IP) per window, on top of the general per-IP API limit.
# Kept in-process: with several API replicas each one counts separately.
MAX_FAILURES = 10
FAILURE_WINDOW_S = 15 * 60
_failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_failures_lock = threading.Lock()
# Failed sign-ins are recorded in the audit log, but anyone can trigger them, with any email. Cap how
# many one address can add per window so the log can't be flooded; the cap itself is recorded once.
MAX_AUDITED_FAILURES_PER_IP = 20
_audited_failures: dict[str, deque[float]] = defaultdict(deque)


class LoginIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=1024)


def _user_out(user) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role, "teams": user.teams}


def _set_session_cookie(response: Response, session: Session, user) -> None:
    raw, expires = auth.start_session(session, user)
    settings = get_settings()
    response.set_cookie(
        auth.COOKIE_NAME, raw, max_age=settings.session_days * 86400, expires=expires, path="/",
        httponly=True, secure=settings.cookie_secure, samesite="lax",
    )


def _recent_failures(key: tuple[str, str], now: float) -> deque[float]:
    q = _failures[key]
    while q and q[0] < now - FAILURE_WINDOW_S:
        q.popleft()
    return q


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, session: Session = Depends(get_session)) -> dict:
    ip = get_client_ip(request)
    normalized = auth.normalize_email(body.email)
    key = (normalized, request.client.host if request.client else "")
    now = time.monotonic()
    with _failures_lock:
        if len(_recent_failures(key, now)) >= MAX_FAILURES:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed sign-ins. Try again later.")
    user = auth.authenticate(session, body.email, body.password)
    if user is None:
        with _failures_lock:
            _recent_failures(key, now).append(now)
        with _failures_lock:
            seen = _audited_failures[ip or ""]
            while seen and seen[0] < now - FAILURE_WINDOW_S:
                seen.popleft()
            seen.append(now)
            audited = len(seen)
        if audited <= MAX_AUDITED_FAILURES_PER_IP:
            record_audit(
                session,
                action="auth.login.failed",
                actor_type="anonymous",  # nobody is signed in; the email is only what was typed
                actor_email=normalized[:254],
                target_type="auth",
                target_id=normalized[:100],
                details={"reason": "invalid_credentials"},
                ip_address=ip,
            )
        elif audited == MAX_AUDITED_FAILURES_PER_IP + 1:
            record_audit(
                session,
                action="auth.login.failed.capped",
                actor_type="anonymous",
                actor_email="(many emails)",
                target_type="auth",
                details={"reason": f"over {MAX_AUDITED_FAILURES_PER_IP} failed sign-ins from this address in "
                                   f"{FAILURE_WINDOW_S // 60} minutes; further failures aren't recorded"},
                ip_address=ip,
            )
        session.commit()
        # Same message whether the email exists or not.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    with _failures_lock:
        _failures.pop(key, None)
    record_audit(
        session,
        action="auth.login.success",
        actor_email=user.email,
        actor_id=user.id,
        target_type="auth",
        target_id=user.email,
        ip_address=ip,
    )
    _set_session_cookie(response, session, user)
    session.commit()
    return {"user": _user_out(user)}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, session: Session = Depends(get_session)) -> None:
    token = request.cookies.get(auth.COOKIE_NAME)
    user = auth.user_for_session(session, token)
    if user is not None:
        record_audit(
            session,
            action="auth.logout",
            actor_email=user.email,
            actor_id=user.id,
            target_type="auth",
            target_id=user.email,
            ip_address=get_client_ip(request),
        )
    auth.end_session(session, token)
    session.commit()
    response.delete_cookie(auth.COOKIE_NAME, path="/")


@router.get("/me")
def me(request: Request, session: Session = Depends(get_session)) -> dict:
    """Who is signed in, and whether the dashboard requires signing in at all."""
    user = auth.user_for_session(session, request.cookies.get(auth.COOKIE_NAME))
    return {"auth_required": auth.login_required(session), "user": _user_out(user) if user else None}


class InviteIn(BaseModel):
    token: str = Field(..., min_length=10, max_length=200)
    password: str = Field(..., min_length=1, max_length=1024)


@router.get("/invite")
def check_invite(token: str, session: Session = Depends(get_session)) -> dict:
    inv = auth.invite_for(session, token)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link has expired or was already used.")
    user = session.get(auth.User, inv.user_id)
    return {"email": user.email, "name": user.name}


@router.post("/invite")
def accept_invite(body: InviteIn, request: Request, response: Response,
                  session: Session = Depends(get_session)) -> dict:
    """Set a password from an invite or reset link, then sign in."""
    try:
        user = auth.accept_invite(session, body.token, body.password)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link has expired or was already used.")
    record_audit(
        session,
        action="auth.invite.accepted",
        actor_email=user.email,
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
        details={"password": "set"},
        ip_address=get_client_ip(request),
    )
    _set_session_cookie(response, session, user)
    session.commit()
    return {"user": _user_out(user)}
