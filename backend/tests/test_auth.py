from datetime import UTC, datetime, timedelta

import pytest

from burnrate import auth
from burnrate.api import auth as auth_api
from burnrate.config import get_settings
from burnrate.models import UserSession

PASSWORD = "correct horse battery"
CSRF = {"X-Requested-With": "burnrate"}


@pytest.fixture(autouse=True)
def _plain_http_cookies(monkeypatch):
    # TestClient talks http://testserver; Secure cookies would never be sent back.
    monkeypatch.setattr(get_settings(), "cookie_secure", False)
    auth_api._failures.clear()


def _login(client, email="ana@acme.io", password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_password_hashing_round_trips_and_salts():
    h = auth.hash_password(PASSWORD)
    assert h.startswith("scrypt$") and PASSWORD not in h
    assert auth.verify_password(PASSWORD, h) and not auth.verify_password("wrong", h)
    assert auth.hash_password(PASSWORD) != h
    assert not auth.verify_password(PASSWORD, "garbage")


def test_dashboard_is_open_until_the_first_user_exists(client, session):
    assert client.get("/api/meta").status_code == 200
    assert client.get("/api/auth/me").json() == {"auth_required": False, "user": None}

    auth.create_user(session, "Ana@Acme.io", PASSWORD, "Ana")
    assert client.get("/api/meta").status_code == 401
    assert client.get("/api/auth/me").json()["auth_required"] is True


def test_login_session_logout(client, session):
    auth.create_user(session, "ana@acme.io", PASSWORD, "Ana")

    assert _login(client, password="nope").status_code == 401
    assert _login(client, email="who@acme.io").json()["detail"] == "Wrong email or password"

    r = _login(client, email="ANA@acme.io ")
    assert r.status_code == 200 and r.json()["user"]["email"] == "ana@acme.io"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/meta").status_code == 200
    assert client.get("/api/auth/me").json()["user"]["name"] == "Ana"
    assert session.query(UserSession).count() == 1

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/meta").status_code == 401
    assert session.query(UserSession).count() == 0


def test_cookie_writes_need_the_csrf_header(client, session):
    auth.create_user(session, "ana@acme.io", PASSWORD)
    _login(client)
    budget = {"name": "All AI", "monthly_limit_usd": 1000}
    assert client.post("/api/budgets", json=budget).status_code == 403
    assert client.post("/api/budgets", json=budget, headers=CSRF).status_code == 201


def test_dashboard_token_still_works_for_scripts(client, session, monkeypatch):
    auth.create_user(session, "ana@acme.io", PASSWORD)
    monkeypatch.setattr(get_settings(), "dashboard_token", "s3cret")
    h = {"Authorization": "Bearer s3cret"}
    assert client.get("/api/meta", headers=h).status_code == 200
    assert client.post("/api/budgets", json={"name": "x", "monthly_limit_usd": 1}, headers=h).status_code == 201


def test_expired_disabled_and_reset_users_lose_access(client, session):
    user = auth.create_user(session, "ana@acme.io", PASSWORD)
    _login(client)
    row = session.query(UserSession).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert client.get("/api/meta").status_code == 401

    _login(client)
    auth.set_disabled(session, user, True)
    assert client.get("/api/meta").status_code == 401
    assert _login(client).status_code == 401
    # The only user is disabled, so nobody can sign in: the dashboard must stay closed, not reopen.
    assert client.get("/api/meta").status_code == 401 and auth.login_required(session)

    auth.set_disabled(session, user, False)
    _login(client)
    auth.set_password(session, user, "a brand new password")
    assert client.get("/api/meta").status_code == 401
    assert _login(client, password="a brand new password").status_code == 200


def test_repeated_failures_are_throttled(client, session):
    auth.create_user(session, "ana@acme.io", PASSWORD)
    for _ in range(auth_api.MAX_FAILURES):
        assert _login(client, password="wrong").status_code == 401
    assert _login(client).status_code == 429  # even the right password, until the window passes


def test_account_validation(session):
    with pytest.raises(ValueError, match="at least"):
        auth.create_user(session, "ana@acme.io", "short")
    with pytest.raises(ValueError, match="valid email"):
        auth.create_user(session, "not-an-email", PASSWORD)
    auth.create_user(session, "ana@acme.io", PASSWORD)
    with pytest.raises(ValueError, match="already exists"):
        auth.create_user(session, "ANA@acme.io", PASSWORD)


def test_cli_user_commands(engine, monkeypatch, capsys):
    import io

    from sqlalchemy.orm import sessionmaker

    from burnrate import cli

    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(cli, "init_db", lambda: None)

    assert cli.main(["list-users"]) == 0
    assert "No users" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert cli.main(["create-user", "ana@acme.io", "--name", "Ana", "--password-stdin"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert cli.main(["reset-password", "ana@acme.io", "--password-stdin"]) == 1
    assert cli.main(["disable-user", "ana@acme.io"]) == 0
    assert cli.main(["enable-user", "nobody@acme.io"]) == 1
    capsys.readouterr()
    assert cli.main(["list-users"]) == 0
    out = capsys.readouterr().out
    assert "ana@acme.io" in out and "disabled" in out and PASSWORD not in out
