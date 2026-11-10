import httpx
import pytest

from burnrate.config import get_settings
from burnrate.models import AppSetting
from burnrate.services import app_settings, jira, secrets

ADMIN = {"Authorization": "Bearer admin-token"}
HOOK = "https://hooks.slack.com/services/T000/B000/abcdefghijkl"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(get_settings(), "dashboard_token", "admin-token")


def _raw(session, key):
    session.expire_all()
    row = session.get(AppSetting, key)
    return row.value if row else None


def test_saved_secrets_are_encrypted_in_the_database(session):
    app_settings.put(session, app_settings.WEBHOOK_KEY, HOOK)
    raw = _raw(session, app_settings.WEBHOOK_KEY)
    assert raw.startswith("enc:v1:") and "hooks.slack.com" not in raw and "abcdefghijkl" not in raw
    assert app_settings.get(session, app_settings.WEBHOOK_KEY) == HOOK
    assert app_settings.secret_problems(session) == []
    # Non-secret settings stay readable as-is.
    app_settings.put(session, "jira.project_key", "OPS")
    assert _raw(session, "jira.project_key") == "OPS"


def test_without_a_key_secrets_are_refused_not_stored_in_plain_text(client, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "secret_key", "")
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"displayName": "Bot", "name": "Ops", "issueTypes": [{"name": "Task"}]}))))

    r = client.put("/api/settings/alerts", json={"webhook_url": HOOK}, headers=ADMIN)
    assert r.status_code == 409 and "BURNRATE_SECRET_KEY" in r.json()["detail"]
    assert _raw(session, app_settings.WEBHOOK_KEY) is None

    j = client.put("/api/settings/jira", json={"site": "acme.atlassian.net", "email": "bot@acme.io",
                                               "api_token": "tok-123", "project_key": "OPS"}, headers=ADMIN)
    assert j.status_code == 409
    assert session.query(AppSetting).filter(AppSetting.key.like("jira.%")).count() == 0  # nothing half-saved
    assert client.get("/api/settings/alerts", headers=ADMIN).json()["encryption"]["key_set"] is False


def test_plain_text_values_from_before_are_encrypted_on_startup(session, monkeypatch):
    session.add(AppSetting(key=app_settings.WEBHOOK_KEY, value=HOOK))  # as saved by an older version
    session.commit()
    assert "isn't encrypted yet" in app_settings.secret_problems(session)[0]
    assert app_settings.get(session, app_settings.WEBHOOK_KEY) == HOOK  # still works meanwhile

    assert app_settings.encrypt_stored_secrets(session) == 1
    assert _raw(session, app_settings.WEBHOOK_KEY).startswith("enc:v1:")
    assert app_settings.get(session, app_settings.WEBHOOK_KEY) == HOOK
    assert app_settings.encrypt_stored_secrets(session) == 0  # already done


def test_wrong_key_reads_as_disconnected_and_says_why(session, monkeypatch):
    jira.save(session, jira.JiraConfig(site="https://acme.atlassian.net", email="bot@acme.io", api_token="tok-123",
                                       project_key="OPS", issue_type="Task"))
    monkeypatch.setattr(get_settings(), "secret_key", secrets.generate_key())  # a different key
    assert jira.load(session) is None
    problems = app_settings.secret_problems(session)
    assert len(problems) == 1 and "Jira API token can't be read" in problems[0]
    before = _raw(session, app_settings.JIRA_TOKEN_KEY)
    assert app_settings.encrypt_stored_secrets(session) == 0
    assert _raw(session, app_settings.JIRA_TOKEN_KEY) == before  # never overwritten


def test_key_rotation(session, monkeypatch):
    old = get_settings().secret_key
    app_settings.put(session, app_settings.WEBHOOK_KEY, HOOK)
    new = secrets.generate_key()
    monkeypatch.setattr(get_settings(), "secret_key", f"{new},{old}")
    assert app_settings.get(session, app_settings.WEBHOOK_KEY) == HOOK  # old key still decrypts
    assert app_settings.encrypt_stored_secrets(session) == 1  # re-encrypted with the new key
    assert app_settings.encrypt_stored_secrets(session) == 0
    monkeypatch.setattr(get_settings(), "secret_key", new)  # old key removed
    assert app_settings.get(session, app_settings.WEBHOOK_KEY) == HOOK


def test_malformed_key_fails_fast(monkeypatch):
    monkeypatch.setattr(get_settings(), "secret_key", "not-a-key")
    with pytest.raises(secrets.SecretKeyInvalid, match="generate-secret-key"):
        secrets.validate()


def test_cli_generates_a_usable_key(capsys):
    from burnrate import cli

    assert cli.main(["generate-secret-key"]) == 0
    key = capsys.readouterr().out.strip()
    from cryptography.fernet import Fernet

    assert Fernet(key.encode()).decrypt(Fernet(key.encode()).encrypt(b"x")) == b"x"
