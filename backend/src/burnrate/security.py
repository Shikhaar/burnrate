import hashlib
import hmac
import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate.config import get_settings
from burnrate.db import get_session
from burnrate.models import ApiKey

KEY_PREFIX = "br_live_"


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
    return key


def require_dashboard_token(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().dashboard_token
    if not expected:
        return
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid dashboard token")
