"""Audit logging service: immutable, append-only record of administrative, auth, and CLI actions.

Tamper-resistance note:
Audit entries are append-only within the application layer. Burnrate exposes no edit or delete
endpoints for audit logs. Operators with direct SQL access to the underlying database retain
raw write access.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from burnrate.config import get_settings
from burnrate.models import AuditLog


def actor_info(who: Any) -> tuple[str, str, int | None]:
    """Returns (actor_type, actor_email, actor_id) from a Principal."""
    if who is not None and getattr(who, "user", None) is not None:
        user = who.user
        return "user", user.email, user.id
    via = getattr(who, "via", None)
    if via == "token":
        return "token", "dashboard token", None
    if via == "open":
        # No users exist yet, so anyone who could reach the dashboard did this.
        return "system", "open access (no users yet)", None
    return "system", "system", None


# Keys that should never have their values logged into audit logs.
SENSITIVE_KEY_SUBSTRINGS = ("secret", "token", "password", "webhook", "credential", "private", "bearer")
# Words callers use to describe a secret without including it; kept as written so "unchanged" stays true.
STATUS_MARKERS = frozenset({"changed", "unchanged", "set", "removed"})
EXEMPT_KEYS = {"project_key", "issue_key", "key_id", "key_name", "prefix", "teams"}

SENSITIVE_URL_PATTERNS = re.compile(
    r"(hooks\.slack\.com|discord\.com/api/webhooks|api\.telegram\.org/bot)",
    re.IGNORECASE,
)
SENSITIVE_PREFIXES = ("sk-", "br_live_", "ghp_", "gho_", "xoxb-", "xoxp-")


def redact_secrets(data: Any) -> Any:
    """Recursively scrub secrets, tokens, passwords, and webhook URLs from audit payloads."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if k_lower in EXEMPT_KEYS:
                sanitized[k] = redact_secrets(v)
            elif (
                any(sub in k_lower for sub in SENSITIVE_KEY_SUBSTRINGS)
                or k_lower in {"api_key", "key"}
            ):
                if v is None or v == "":
                    sanitized[k] = None
                elif v in STATUS_MARKERS:
                    sanitized[k] = v  # already a description ("unchanged"), not a secret
                else:
                    sanitized[k] = "changed"
            else:
                sanitized[k] = redact_secrets(v)
        return sanitized

    if isinstance(data, list):
        return [redact_secrets(item) for item in data]

    if isinstance(data, str):
        if SENSITIVE_URL_PATTERNS.search(data):
            return "changed"
        # Only full secrets (e.g. sk-..., br_live_...) are redacted; short prefixes like "br_live_ab" are preserved.
        if len(data) > 20 and any(data.startswith(prefix) for prefix in SENSITIVE_PREFIXES):
            return "changed"
        return data

    return data


def get_client_ip(request: Request | None) -> str | None:
    """Extract client IP, only trusting X-Forwarded-For if request comes from a configured trusted proxy."""
    if not request or not request.client:
        return None

    direct_ip = request.client.host
    settings = get_settings()

    if settings.trusted_proxies:
        trusted = {ip.strip() for ip in settings.trusted_proxies.split(",") if ip.strip()}
        if direct_ip in trusted:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                # Each proxy appends the address it received the request from, so only the right-hand end
                # is trustworthy: walk right to left past our own proxies and take the first other address.
                # The left-most entry is whatever the client sent and can be forged.
                hops = [h.strip() for h in forwarded.split(",") if h.strip()]
                for hop in reversed(hops):
                    if hop not in trusted:
                        return hop[:45]

    return direct_ip


def record_audit(
    session: Session,
    action: str,
    actor_email: str,
    actor_type: str = "user",
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Record an audit event. Always redacts secrets from details before saving."""
    sanitized_details = redact_secrets(details or {})
    entry = AuditLog(
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_email=actor_email,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        details=sanitized_details,
        ip_address=ip_address,
    )
    session.add(entry)
    return entry


def _aware(ts: datetime | None) -> datetime | None:
    """Ensure datetime has UTC timezone."""
    return ts.replace(tzinfo=UTC) if ts is not None and ts.tzinfo is None else ts


def list_audit_logs(
    session: Session,
    limit: int = 50,
    offset: int = 0,
    action: str | None = None,
    actor: str | None = None,
    days: int = 30,
) -> dict:
    """Return paginated audit log entries with optional action/actor filtering."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    cutoff = datetime.now(UTC) - timedelta(days=max(1, days))

    query = select(AuditLog).where(AuditLog.timestamp >= cutoff)
    count_query = select(func.count(AuditLog.id)).where(AuditLog.timestamp >= cutoff)

    if action:
        act = action.strip()
        query = query.where(AuditLog.action.ilike(f"%{act}%"))
        count_query = count_query.where(AuditLog.action.ilike(f"%{act}%"))

    if actor:
        actr = actor.strip()
        query = query.where(AuditLog.actor_email.ilike(f"%{actr}%"))
        count_query = count_query.where(AuditLog.actor_email.ilike(f"%{actr}%"))

    total = session.scalar(count_query) or 0
    items = session.scalars(query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit)).all()

    return {
        "total": total,
        "items": [
            {
                "id": a.id,
                "timestamp": _aware(a.timestamp).isoformat() if a.timestamp else None,
                "actor_type": a.actor_type,
                "actor_id": a.actor_id,
                "actor_email": a.actor_email,
                "action": a.action,
                "target_type": a.target_type,
                "target_id": a.target_id,
                "details": a.details,
                "ip_address": a.ip_address,
            }
            for a in items
        ],
    }

