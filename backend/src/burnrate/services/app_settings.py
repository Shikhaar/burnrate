"""Settings stored in the database so admins can change them from the dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from burnrate.config import get_settings
from burnrate.models import AppSetting

WEBHOOK_KEY = "alerts.webhook_url"
LAST_RUN_KEY = "alerts.last_run_at"
LAST_RUN_ERROR_KEY = "alerts.last_run_error"
MAX_URL_LENGTH = 500


def get(session: Session, key: str) -> str | None:
    row = session.get(AppSetting, key)
    return row.value if row else None


def put(session: Session, key: str, value: str | None) -> None:
    row = session.get(AppSetting, key)
    if value is None:
        if row:
            session.delete(row)
    elif row:
        row.value = value
        row.updated_at = datetime.now(UTC)
    else:
        session.add(AppSetting(key=key, value=value))
    session.commit()


def webhook_url(session: Session) -> tuple[str | None, str | None]:
    """(url, source): the dashboard setting wins over BURNRATE_ALERT_WEBHOOK_URL."""
    saved = get(session, WEBHOOK_KEY)
    if saved:
        return saved, "dashboard"
    env = get_settings().alert_webhook_url
    return (env, "environment") if env else (None, None)


def validate_webhook(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if len(url) > MAX_URL_LENGTH or parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise ValueError("Paste the full https:// webhook link Slack gave you.")
    return url


def mask_webhook(url: str) -> str:
    """Enough to recognise which webhook it is, never enough to post to it."""
    parts = urlsplit(url)
    tail = url.rstrip("/")[-4:]
    return f"{parts.scheme}://{parts.netloc}/…{tail}"


def is_slack(url: str) -> bool:
    return urlsplit(url).netloc.lower() == "hooks.slack.com"
