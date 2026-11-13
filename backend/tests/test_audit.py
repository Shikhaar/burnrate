from unittest.mock import MagicMock

from sqlalchemy.orm import sessionmaker

from burnrate import auth, cli
from burnrate.config import get_settings
from burnrate.models import AuditLog
from burnrate.services.audit import get_client_ip, redact_secrets


def test_redact_secrets_scrubs_sensitive_data():
    raw = {
        "email": "admin@example.com",
        "password": "super-secret-password",
        "api_token": "jira_api_token_123",
        "webhook_url": "https://hooks.slack.com/services/T00/B00/X123",
        "secret": "my-secret-key",
        "project_key": "FIN",  # Jira project key should NOT be redacted
        "prefix": "br_live_ab",
        "nested": {
            "bearer_token": "xyz789",
            "normal_field": "keep-me",
        },
    }
    redacted = redact_secrets(raw)
    assert redacted["email"] == "admin@example.com"
    assert redacted["password"] == "changed"
    assert redacted["api_token"] == "changed"
    assert redacted["webhook_url"] == "changed"
    assert redacted["secret"] == "changed"
    assert redacted["project_key"] == "FIN"
    assert redacted["prefix"] == "br_live_ab"
    assert redacted["nested"]["bearer_token"] == "changed"
    assert redacted["nested"]["normal_field"] == "keep-me"
    # Descriptions of a secret stay as written, so the trail can say a token was NOT changed.
    described = {"api_token": "unchanged", "password": "set"}
    assert redact_secrets(described) == described


def test_redact_secrets_string_detection():
    # Direct slack webhook string
    assert redact_secrets("https://hooks.slack.com/services/T00/B00/SECRET") == "changed"
    # OpenAI key prefix
    assert redact_secrets("sk-proj-1234567890abcdef") == "changed"
    # Ordinary string
    assert redact_secrets("search-service") == "search-service"


def test_trusted_proxy_ip_handling(monkeypatch):
    import burnrate.services.audit as audit_module

    class DummyClient:
        host = "10.0.0.1"

    class DummyRequest:
        client = DummyClient()
        headers = {"x-forwarded-for": "203.0.113.195, 10.0.0.1"}

    req = DummyRequest()

    # Case 1: Proxy is not in trusted_proxies -> returns direct client host
    monkeypatch.setattr(audit_module, "get_settings", lambda: MagicMock(trusted_proxies=""))
    assert get_client_ip(req) == "10.0.0.1"

    # Case 2: Proxy is trusted -> returns client IP from X-Forwarded-For
    monkeypatch.setattr(audit_module, "get_settings", lambda: MagicMock(trusted_proxies="10.0.0.1,127.0.0.1"))
    assert get_client_ip(req) == "203.0.113.195"

    # Case 3: a client forges the header. Our proxy appends the address it really saw, on the right;
    # the forged value on the left must not be believed.
    class Forged(DummyRequest):
        headers = {"x-forwarded-for": "1.2.3.4, 198.51.100.7"}

    assert get_client_ip(Forged()) == "198.51.100.7"


def test_login_success_and_failure_audited(client, session):
    user = auth.create_user(session, "alice@example.com", "correct-password-123", role="admin")

    # Failed login
    res = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrong-password"})
    assert res.status_code == 401

    failed_log = session.query(AuditLog).filter(AuditLog.action == "auth.login.failed").first()
    assert failed_log is not None
    assert failed_log.actor_email == "alice@example.com"
    assert failed_log.actor_type == "anonymous"  # nobody is signed in; the email is only what was typed
    assert failed_log.details == {"reason": "invalid_credentials"}

    # Successful login
    res = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct-password-123"})
    assert res.status_code == 200

    success_log = session.query(AuditLog).filter(AuditLog.action == "auth.login.success").first()
    assert success_log is not None
    assert success_log.actor_email == "alice@example.com"
    assert success_log.actor_id == user.id


def test_cli_actions_logged_as_server_command(client, session, engine, monkeypatch):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "SessionLocal", Session)
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "sync_registry", lambda s: None)

    # 1. CLI create-key
    assert cli.main(["create-key", "cli-test-key"]) == 0
    key_log = session.query(AuditLog).filter(AuditLog.action == "key.create").first()
    assert key_log is not None
    assert key_log.actor_type == "cli"
    assert key_log.actor_email == "server command"
    assert key_log.details["name"] == "cli-test-key"
    assert "secret" not in key_log.details

    # 2. CLI create-user
    monkeypatch.setattr(cli, "_read_password", lambda _: "password-123")
    assert cli.main(["create-user", "charlie@example.com", "--role", "member"]) == 0
    user_log = session.query(AuditLog).filter(AuditLog.action == "user.create").first()
    assert user_log is not None
    assert user_log.actor_type == "cli"
    assert user_log.actor_email == "server command"
    assert user_log.details["email"] == "charlie@example.com"
    assert "password" not in user_log.details


def test_audit_logs_endpoint_access_and_filtering(client, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "cookie_secure", False)

    # Setup admin and member
    auth.create_user(session, "admin@example.com", "admin-pass-123", role="admin")
    auth.create_user(session, "member@example.com", "member-pass-123", role="member")

    # Member cannot access audit logs
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": "member@example.com", "password": "member-pass-123"})
    assert r.status_code == 200
    res = client.get("/api/audit-logs")
    assert res.status_code == 403

    # Admin can access audit logs
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin-pass-123"})
    assert r.status_code == 200
    res = client.get("/api/audit-logs")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] > 0
    assert any(item["action"].startswith("auth.login") for item in data["items"])

    # Filtering by action returns only matching entries
    res_filter = client.get("/api/audit-logs?action=auth.login.success")
    assert res_filter.status_code == 200
    assert res_filter.json()["items"] and all(i["action"] == "auth.login.success" for i in res_filter.json()["items"])

    # Ensure no write endpoints exist
    assert client.post("/api/audit-logs", json={}).status_code in {404, 405}
    assert client.delete("/api/audit-logs/1").status_code in {404, 405}


ADMIN_TOKEN = {"Authorization": "Bearer audit-token"}


def _actions(session):
    session.expire_all()
    return [a.action for a in session.query(AuditLog).order_by(AuditLog.id)]


def test_token_and_open_access_are_labelled_differently(client, session, monkeypatch):
    # No users and no token: open local demo.
    client.post("/api/budgets", json={"name": "open", "monthly_limit_usd": 1})
    # With a token set, the same action is attributed to the token.
    monkeypatch.setattr(get_settings(), "dashboard_token", "audit-token")
    client.post("/api/budgets", json={"name": "tok", "monthly_limit_usd": 1}, headers=ADMIN_TOKEN)
    session.expire_all()
    rows = session.query(AuditLog).filter(AuditLog.action == "budget.create").order_by(AuditLog.id).all()
    assert [(r.actor_type, r.actor_email) for r in rows] == [
        ("system", "open access (no users yet)"), ("token", "dashboard token")]


def test_failed_logins_cannot_flood_the_log(client, session):
    from burnrate.api import auth as auth_api

    auth_api._failures.clear()
    auth_api._audited_failures.clear()
    for i in range(auth_api.MAX_AUDITED_FAILURES_PER_IP + 15):
        client.post("/api/auth/login", json={"email": f"guess{i}@example.com", "password": "nope"})
    actions = _actions(session)
    assert actions.count("auth.login.failed") == auth_api.MAX_AUDITED_FAILURES_PER_IP
    assert actions.count("auth.login.failed.capped") == 1


def test_no_duplicate_or_empty_entries(client, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "dashboard_token", "audit-token")
    key = client.post("/api/keys", json={"name": "svc"}, headers=ADMIN_TOKEN).json()["key"]
    client.post(f"/api/keys/{key['id']}/revoke", headers=ADMIN_TOKEN)
    client.post(f"/api/keys/{key['id']}/revoke", headers=ADMIN_TOKEN)  # already revoked
    assert _actions(session).count("key.revoke") == 1

    user = auth.create_user(session, "nia@example.com", "correct-password-123", role="viewer")
    client.patch(f"/api/users/{user.id}", json={"role": "viewer"}, headers=ADMIN_TOKEN)  # no change
    assert "user.update" not in _actions(session)
    client.patch(f"/api/users/{user.id}", json={"role": "member"}, headers=ADMIN_TOKEN)
    entry = session.query(AuditLog).filter(AuditLog.action == "user.update").one()
    assert entry.details == {"role": {"old": "viewer", "new": "member"}}


def test_cli_revoke_twice_logs_once(engine, session, monkeypatch, capsys):
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(cli, "init_db", lambda: None)
    assert cli.main(["create-key", "svc"]) == 0
    assert cli.main(["revoke-key", "1"]) == 0
    assert cli.main(["revoke-key", "1"]) == 0
    assert "Already revoked" in capsys.readouterr().out
    assert _actions(session).count("key.revoke") == 1
