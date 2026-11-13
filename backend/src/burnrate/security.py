import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate.config import get_settings
from burnrate.db import get_session
from burnrate.models import ApiKey

KEY_PREFIX = "br_live_"
# How stale `last_used_at` may get: enough to tell a live key from an abandoned one, without a write per batch.
LAST_USED_RESOLUTION = timedelta(minutes=5)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_api_key(session: Session, name: str) -> str:
    """Create an ingest key and return the raw value. Only the hash is stored."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    session.add(ApiKey(name=name, prefix=raw[: len(KEY_PREFIX) + 4], key_hash=hash_key(raw)))
    session.commit()
    return raw


def require_ingest_key(
    x_api_key: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> ApiKey:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key header")
    key = session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(x_api_key)))
    if key is None or key.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked API key")
    now = datetime.now(UTC)
    last = _aware(key.last_used_at)
    if last is None or now - last >= LAST_USED_RESOLUTION:
        key.last_used_at = now
        session.commit()
    return key


def _aware(ts: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; everything is stored as UTC.
    return ts.replace(tzinfo=UTC) if ts is not None and ts.tzinfo is None else ts


def list_api_keys(session: Session) -> list[ApiKey]:
    return list(session.scalars(select(ApiKey).order_by(ApiKey.id)))


def find_api_key(session: Session, key_ref: str) -> ApiKey | None:
    """A key by id (from `burnrate list-keys`) or by the raw key itself."""
    ref = key_ref.strip()
    if ref.isdigit():
        return session.get(ApiKey, int(ref))
    return session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(ref)))


def revoke_api_key(session: Session, key_ref: str) -> ApiKey | None:
    """Revoke by id (from `burnrate list-keys`) or by the raw key itself, e.g. one found in a leak.

    Takes effect on the next request: every request re-checks the key. Returns None if nothing matches.
    Revoking twice keeps the original revocation time.
    """
    key = find_api_key(session, key_ref)
    if key is not None and key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        session.commit()
    return key


def require_dashboard_token(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().dashboard_token
    if not expected:
        return
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid dashboard token")
