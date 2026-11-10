"""Settings stored in the database so admins can change them from the dashboard."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from burnrate.config import get_settings
from burnrate.models import AppSetting
from burnrate.services import secrets

WEBHOOK_KEY = "alerts.webhook_url"
LAST_RUN_KEY = "alerts.last_run_at"
LAST_RUN_ERROR_KEY = "alerts.last_run_error"
MAX_URL_LENGTH = 500
JIRA_TOKEN_KEY = "jira.api_token"
# Stored encrypted (see burnrate.services.secrets). Everything else is plain configuration.
SECRET_KEYS = frozenset({WEBHOOK_KEY, JIRA_TOKEN_KEY})
log = logging.getLogger("burnrate.settings")


def get(session: Session, key: str) -> str | None:
    """The setting's value, decrypted. An undecryptable secret reads as unset (see `secret_problems`)."""
    row = session.get(AppSetting, key)
    if row is None:
        return None
    if key not in SECRET_KEYS:
        return row.value
    try:
        return secrets.decrypt(row.value)
    except secrets.Undecryptable as e:
        log.warning("%s: %s", key, e)
        return None


def put(session: Session, key: str, value: str | None) -> None:
    """Save a setting. Secrets are encrypted first; raises SecretKeyMissing if no key is configured."""
    if value is not None and key in SECRET_KEYS:
        value = secrets.encrypt(value)
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


def secret_problems(session: Session) -> list[str]:
    """Saved secrets Burnrate can't read, and saved secrets still in plain text, for the settings page."""
    problems = []
    for key in sorted(SECRET_KEYS):
        row = session.get(AppSetting, key)
        if row is None:
            continue
        label = "Slack link" if key == WEBHOOK_KEY else "Jira API token"
        if not secrets.is_encrypted(row.value):
            problems.append(f"The saved {label} isn't encrypted yet. Set BURNRATE_SECRET_KEY and restart Burnrate.")
        else:
            try:
                secrets.decrypt(row.value)
            except secrets.Undecryptable as e:
                problems.append(f"The saved {label} can't be read: {e} Reconnect it in Settings.")
    return problems


def encrypt_stored_secrets(session: Session) -> int:
    """On startup: encrypt secrets saved before encryption existed, and re-encrypt after a key rotation."""
    if not secrets.configured():
        if any(session.get(AppSetting, k) and not secrets.is_encrypted(session.get(AppSetting, k).value)
               for k in SECRET_KEYS):
            log.warning("Secrets saved from Settings are stored unencrypted; set BURNRATE_SECRET_KEY to encrypt them.")
        return 0
    changed = 0
    for key in SECRET_KEYS:
        row = session.get(AppSetting, key)
        if row is None:
            continue
        try:
            new = secrets.reencrypt(row.value)
        except secrets.Undecryptable:
            continue  # reported by secret_problems; never overwrite what we can't read
        if new is not None:
            row.value = new
            changed += 1
    if changed:
        session.commit()
        log.info("encrypted %d saved secret(s)", changed)
    return changed
