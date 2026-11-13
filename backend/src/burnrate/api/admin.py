"""Admin endpoints behind the dashboard's Settings pages: users and ingest keys."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate import auth
from burnrate.auth import Principal, require_admin
from burnrate.db import get_session
from burnrate.models import ApiKey, User
from burnrate.security import create_api_key, find_api_key, hash_key, list_api_keys, revoke_api_key
from burnrate.services.audit import actor_info, get_client_ip, list_audit_logs, record_audit

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
def _require_superadmin_for(who: Principal, *roles: str) -> None:
    """Admins and super admins can only be added, changed or reset by a super admin."""
    if who.role != "superadmin" and any(r in auth.ADMIN_ROLES for r in roles):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can add or change admins.")


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
def invite_user(body: UserIn, request: Request, session: Session = Depends(get_session),
                who: Principal = Depends(require_admin)) -> dict:
    _require_superadmin_for(who, body.role)
    try:
        user = auth.create_user(session, body.email, None, body.name, body.role, body.teams)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    act_type, act_email, act_id = actor_info(who)
    record_audit(
        session,
        action="user.create",
        actor_type=act_type,
        actor_email=act_email,
        actor_id=act_id,
        target_type="user",
        target_id=str(user.id),
        details={"email": user.email, "name": user.name, "role": user.role, "teams": user.teams},
        ip_address=get_client_ip(request),
    )
    session.commit()
    return {"user": user_out(user), **_invite(session, user)}


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, request: Request, session: Session = Depends(get_session),
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
    if role in auth.ADMIN_ROLES:
        teams = None  # admins always see every team
    disabled = body.disabled if "disabled" in changes and body.disabled is not None else user.disabled_at is not None
    is_self = who.user is not None and who.user.id == user.id
    if is_self and (disabled or role != user.role):
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't disable yourself or change your own role.")
    if not is_self:
        _require_superadmin_for(who, user.role, role)
    was_super = user.role == "superadmin" and user.disabled_at is None and user.teams is None
    stays_super = role == "superadmin" and not disabled and teams is None
    if was_super and not stays_super and auth.active_superadmins(session) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Keep at least one active super admin.")
    was_admin = user.role in auth.ADMIN_ROLES and user.disabled_at is None and user.teams is None
    stays_admin = role in auth.ADMIN_ROLES and not disabled and teams is None
    if was_admin and not stays_admin and auth.active_admins(session) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Keep at least one active admin.")

    diff = {}
    if "name" in changes:
        new_name = (body.name or "").strip() or None
        diff["name"] = {"old": user.name, "new": new_name}
        user.name = new_name
    if role != user.role:
        diff["role"] = {"old": user.role, "new": role}
    if teams != user.teams:
        diff["teams"] = {"old": user.teams, "new": teams}
    user.role, user.teams = role, teams
    if disabled != (user.disabled_at is not None):
        diff["disabled"] = {"old": user.disabled_at is not None, "new": disabled}
        auth.set_disabled(session, user, disabled)  # commits; disabling also ends their sessions

    if not diff:
        session.commit()
        return {"user": user_out(user)}  # a save with no changes isn't an event
    act_type, act_email, act_id = actor_info(who)
    record_audit(
        session,
        action="user.update",
        actor_type=act_type,
        actor_email=act_email,
        actor_id=act_id,
        target_type="user",
        target_id=str(user.id),
        details=diff,
        ip_address=get_client_ip(request),
    )
    session.commit()
    return {"user": user_out(user)}


@router.post("/users/{user_id}/invite")
def new_invite_link(user_id: int, request: Request, session: Session = Depends(get_session),
                    who: Principal = Depends(require_admin)) -> dict:
    """A fresh link to set a password: for a new user who lost theirs, or to reset a forgotten password."""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if who.user is None or who.user.id != user.id:
        _require_superadmin_for(who, user.role)  # a reset link for an admin would hand over their account
    if user.disabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Enable this user first.")
    act_type, act_email, act_id = actor_info(who)
    record_audit(
        session,
        action="user.invite.renew",
        actor_type=act_type,
        actor_email=act_email,
        actor_id=act_id,
        target_type="user",
        target_id=str(user.id),
        details={"email": user.email},
        ip_address=get_client_ip(request),
    )
    session.commit()
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
def new_key(body: KeyIn, request: Request, session: Session = Depends(get_session),
            who: Principal = Depends(require_admin)) -> dict:
    raw = create_api_key(session, body.name.strip())
    key = session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(raw)))
    act_type, act_email, act_id = actor_info(who)
    record_audit(
        session,
        action="key.create",
        actor_type=act_type,
        actor_email=act_email,
        actor_id=act_id,
        target_type="api_key",
        target_id=str(key.id),
        details={"name": key.name, "prefix": key.prefix},
        ip_address=get_client_ip(request),
    )
    session.commit()
    return {"key": key_out(key), "secret": raw}  # the only time the full key is returned


@router.post("/keys/{key_id}/revoke")
def revoke_key(key_id: int, request: Request, session: Session = Depends(get_session),
               who: Principal = Depends(require_admin)) -> dict:
    existing = find_api_key(session, str(key_id))
    already_revoked = existing is not None and existing.revoked_at is not None
    key = revoke_api_key(session, str(key_id))
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    if already_revoked:
        return {"key": key_out(key)}  # nothing changed, so nothing to record
    act_type, act_email, act_id = actor_info(who)
    record_audit(
        session,
        action="key.revoke",
        actor_type=act_type,
        actor_email=act_email,
        actor_id=act_id,
        target_type="api_key",
        target_id=str(key.id),
        details={"name": key.name, "prefix": key.prefix},
        ip_address=get_client_ip(request),
    )
    session.commit()
    return {"key": key_out(key)}


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(key_id: int, request: Request, session: Session = Depends(get_session),
               who: Principal = Depends(require_admin)) -> Response:
    """Remove a revoked key from the list. Active keys must be revoked first, so deleting never breaks an app."""
    key = session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    if key.revoked_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Revoke this key before deleting it.")
    act_type, act_email, act_id = actor_info(who)
    record_audit(
        session,
        action="key.delete",
        actor_type=act_type,
        actor_email=act_email,
        actor_id=act_id,
        target_type="api_key",
        target_id=str(key.id),
        details={"name": key.name, "prefix": key.prefix},
        ip_address=get_client_ip(request),
    )
    session.delete(key)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- audit logs (read-only; append-only via application) -------------------------------------------------
@router.get("/audit-logs")
def get_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = None,
    actor: str | None = None,
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> dict:
    """List audit log entries. Admin only. No edit or delete endpoints exist."""
    return list_audit_logs(session, limit=limit, offset=offset, action=action, actor=actor, days=days)

