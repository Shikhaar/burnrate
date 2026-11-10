"""Encryption for secrets saved from the dashboard (the Slack webhook link, the Jira API token).

Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using `BURNRATE_SECRET_KEY`, which lives
in the server's environment, never in the database. A stolen database or backup is then useless for
posting to Slack or using the Jira account.

Rotation: set `BURNRATE_SECRET_KEY=<new>,<old>`. The first key encrypts; every listed key decrypts,
and values are re-encrypted with the first key on the next start. Remove the old key afterwards.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from burnrate.config import get_settings

PREFIX = "enc:v1:"
log = logging.getLogger("burnrate.secrets")


class SecretKeyMissing(Exception):
    """Raised when asked to store a secret but no BURNRATE_SECRET_KEY is configured."""


class SecretKeyInvalid(ValueError):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


def _keys() -> list[str]:
    return [k.strip() for k in get_settings().secret_key.split(",") if k.strip()]


def _fernet() -> MultiFernet | None:
    keys = _keys()
    if not keys:
        return None
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except (ValueError, TypeError):
        raise SecretKeyInvalid(
            "BURNRATE_SECRET_KEY isn't a valid key. Generate one with `burnrate generate-secret-key`."
        ) from None


def configured() -> bool:
    return _fernet() is not None


def validate() -> None:
    """Fail at startup, not at the first alert, if the configured key is malformed."""
    _fernet()


def is_encrypted(stored: str) -> bool:
    return stored.startswith(PREFIX)


def encrypt(value: str) -> str:
    f = _fernet()
    if f is None:
        raise SecretKeyMissing(
            "Set BURNRATE_SECRET_KEY on the Burnrate server first, so this secret is stored encrypted. "
            "Generate a key with `burnrate generate-secret-key`."
        )
    return PREFIX + f.encrypt(value.encode()).decode()


class Undecryptable(Exception):
    """The stored value was encrypted with a key that isn't configured any more."""


def decrypt(stored: str) -> str:
    """Return the plain value. Values saved before encryption existed are returned as they are."""
    if not is_encrypted(stored):
        return stored
    f = _fernet()
    if f is None:
        raise Undecryptable("BURNRATE_SECRET_KEY isn't set.")
    try:
        return f.decrypt(stored.removeprefix(PREFIX).encode()).decode()
    except InvalidToken:
        raise Undecryptable("it can't be decrypted with BURNRATE_SECRET_KEY. Was the key changed?") from None


def reencrypt(stored: str) -> str | None:
    """The value encrypted with the current first key, or None if nothing needs to change.

    Raises Undecryptable if no configured key can read it; callers must leave such values untouched.
    """
    f = _fernet()
    if f is None:
        return None
    if not is_encrypted(stored):
        return encrypt(stored)
    token = stored.removeprefix(PREFIX).encode()
    try:
        Fernet(_keys()[0].encode()).decrypt(token)
        return None  # already on the current key
    except InvalidToken:
        pass
    try:
        return PREFIX + f.rotate(token).decode()  # readable with an older listed key: move it to the new one
    except InvalidToken:
        raise Undecryptable("it can't be decrypted with BURNRATE_SECRET_KEY. Was the key changed?") from None
