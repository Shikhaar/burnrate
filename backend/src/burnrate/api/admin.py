"""Admin endpoints behind the dashboard's Settings pages: users and ingest keys."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate import auth
from burnrate.auth import Principal, require_admin
from burnrate.db import get_session
from burnrate.models import ApiKey, User
from burnrate.security import create_api_key, list_api_keys, revoke_api_key

router = APIRouter(prefix="/api", tags=["admin"], dependencies=[Depends(require_admin)])


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "teams": u.teams,
            "created_at": _iso(u.created_at), "last_login_at": _iso(u.last_login_at),
            "disabled": u.disabled_at is not None, "invite_pending": not u.password_hash}


def _invite(session: Session, user: User) -> dict:
    raw, expires = auth.issue_invite(session, user)
    # A path, not a full URL: the dashboard knows its own address, the API may not.
    return {"invite_path": f"/invite?token={raw}", "invite_expires_at": expires.isoformat()}


# ---- users -----------------------------------------------------------------------------------------------
class UserIn(BaseModel):
    email: str = Field(..., max_length=254)
    name: str | None = Field(None, max_length=100)
    role: str = "member"
    teams: list[str] | None = None  # None = every team


class UserPatch(BaseModel):
    name: str | None = Field(None, max_length=100)
    role: str | None = None
    teams: list[str] | None = None
    disabled: bool | None = None


@router.get("/users")
def list_users(session: Session = Depends(get_session)) -> dict:
    return {"items": [user_out(u) for u in session.scalars(select(User).order_by(User.id))]}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def invite_user(body: UserIn, session: Session = Depends(get_session)) -> dict:
    try:
        user = auth.create_user(session, body.email, None, body.name, body.role, body.teams)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    return {"user": user_out(user), **_invite(session, user)}


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, session: Session = Depends(get_session),
                who: Principal = Depends(require_admin)) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    changes = body.model_fields_set
    role = body.role if "role" in changes and body.role else user.role
    try:
        teams = auth.clean_teams(body.teams) if "teams" in changes else user.teams
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    if role not in auth.ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Role must be one of: {', '.join(auth.ROLES)}.")
    if role == "admin":
        teams = None  # admins always see every team
    disabled = body.disabled if "disabled" in changes and body.disabled is not None else user.disabled_at is not None
    if who.user is not None and who.user.id == user.id and (disabled or role != "admin"):
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't disable yourself or remove your own admin role.")
    was_admin = user.role == "admin" and user.disabled_at is None and user.teams is None
    stays_admin = role == "admin" and not disabled and teams is None
    if was_admin and not stays_admin and auth.active_admins(session) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Keep at least one active admin.")

    if "name" in changes:
        user.name = (body.name or "").strip() or None
    user.role, user.teams = role, teams
    if disabled != (user.disabled_at is not None):
        auth.set_disabled(session, user, disabled)  # commits; disabling also ends their sessions
    session.commit()
    return {"user": user_out(user)}


@router.post("/users/{user_id}/invite")
def new_invite_link(user_id: int, session: Session = Depends(get_session)) -> dict:
    """A fresh link to set a password: for a new user who lost theirs, or to reset a forgotten password."""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.disabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Enable this user first.")
    return {"user": user_out(user), **_invite(session, user)}


# ---- ingest keys -----------------------------------------------------------------------------------------
class KeyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


def key_out(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "created_at": _iso(k.created_at),
            "last_used_at": _iso(k.last_used_at), "revoked_at": _iso(k.revoked_at)}


@router.get("/keys")
def list_keys(session: Session = Depends(get_session)) -> dict:
    return {"items": [key_out(k) for k in list_api_keys(session)]}


@router.post("/keys", status_code=status.HTTP_201_CREATED)
def new_key(body: KeyIn, session: Session = Depends(get_session)) -> dict:
    raw = create_api_key(session, body.name.strip())
    key = session.scalar(select(ApiKey).order_by(ApiKey.id.desc()).limit(1))
    return {"key": key_out(key), "secret": raw}  # the only time the full key is returned


@router.post("/keys/{key_id}/revoke")
def revoke_key(key_id: int, session: Session = Depends(get_session)) -> dict:
    key = revoke_api_key(session, str(key_id))
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    return {"key": key_out(key)}
