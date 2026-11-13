import pytest
from sqlalchemy import select

from burnrate import auth
from burnrate.api import auth as auth_api
from burnrate.config import get_settings
from burnrate.models import UsageEvent
from burnrate.seed import generate

PASSWORD = "correct horse battery"
CSRF = {"X-Requested-With": "burnrate"}
ADMIN_TOKEN = {"Authorization": "Bearer admin-token"}


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(get_settings(), "cookie_secure", False)
    monkeypatch.setattr(get_settings(), "dashboard_token", "admin-token")
    auth_api._failures.clear()


@pytest.fixture
def seeded(session):
    generate(session, days=30, scale=0.3)
    return session


def _sign_in_as(client, session, email, role="member", teams=None):
    auth.create_user(session, email, PASSWORD, role=role, teams=teams)
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200


def test_team_limited_user_never_sees_other_teams(client, seeded):
    teams = sorted({t for t in seeded.scalars(select(UsageEvent.team).distinct()) if t})
    assert len(teams) >= 3
    mine = teams[0]
    admin = client.get("/api/overview?days=30", headers=ADMIN_TOKEN).json()
    by_team = {i["key"]: i for i in client.get("/api/breakdown?dimension=team&days=30&limit=50",
                                                 headers=ADMIN_TOKEN).json()["items"]}

    _sign_in_as(client, seeded, "pat@acme.io", teams=[mine])

    ov = client.get("/api/overview?days=30").json()
    assert ov["total_cost_usd"] == pytest.approx(by_team[mine]["cost_usd"])
    assert ov["total_cost_usd"] < admin["total_cost_usd"]
    assert ov["request_count"] == by_team[mine]["requests"]

    assert [i["key"] for i in client.get("/api/breakdown?dimension=team&days=30").json()["items"]] == [mine]
    assert client.get("/api/meta").json()["teams"] == [mine]
    events = client.get("/api/events?limit=200").json()
    assert events["total"] == by_team[mine]["requests"]
    assert {e["team"] for e in events["items"]} == {mine}
    ts = client.get("/api/timeseries?group_by=team&days=30").json()
    assert ts["series_keys"] == [mine]
    # Everything else still works, on filtered data.
    for path in ("/api/anomalies", "/api/forecast", "/api/insights", "/api/pricing", "/api/budgets"):
        assert client.get(path).status_code == 200, path
    # Views that mix every team's data are off limits.
    for path in ("/api/reconciliation", "/api/alerts"):
        assert client.get(path).status_code == 403, path
    assert client.post("/api/alerts/evaluate", headers=CSRF).status_code == 403
    # Budgets: only for their own team.
    assert client.post("/api/budgets", json={"name": "all", "monthly_limit_usd": 5}, headers=CSRF).status_code == 403
    ok = client.post("/api/budgets", json={"name": "mine", "monthly_limit_usd": 5, "scope_dimension": "team",
                                           "scope_value": mine}, headers=CSRF)
    assert ok.status_code == 201
    other = client.post("/api/budgets", json={"name": "all spend", "monthly_limit_usd": 9}, headers=ADMIN_TOKEN).json()
    assert [b["name"] for b in client.get("/api/budgets").json()["items"]] == ["mine"]
    assert client.delete(f"/api/budgets/{other['id']}", headers=CSRF).status_code == 404


def test_roles_limit_writes_and_admin_pages(client, session):
    _sign_in_as(client, session, "vic@acme.io", role="viewer")
    assert client.get("/api/overview").status_code == 200
    assert client.post("/api/budgets", json={"name": "x", "monthly_limit_usd": 1}, headers=CSRF).status_code == 403
    assert client.get("/api/users").status_code == 403
    assert client.get("/api/auth/me").json()["user"]["role"] == "viewer"

    _sign_in_as(client, session, "mo@acme.io", role="member")
    assert client.post("/api/budgets", json={"name": "x", "monthly_limit_usd": 1}, headers=CSRF).status_code == 201
    for path in ("/api/users", "/api/keys", "/api/settings/alerts"):
        assert client.get(path).status_code == 403, path

    _sign_in_as(client, session, "ada@acme.io", role="admin")
    for path in ("/api/users", "/api/keys", "/api/settings/alerts"):
        assert client.get(path).status_code == 200, path


def test_invite_flow_and_admin_guards(client, session):
    _sign_in_as(client, session, "ada@acme.io", role="admin")
    r = client.post("/api/users", json={"email": "New@Acme.io", "name": "Nia", "role": "viewer",
                                        "teams": ["search"]}, headers=CSRF)
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["invite_pending"] and body["invite_path"].startswith("/invite?token=")
    token = body["invite_path"].split("=", 1)[1]
    nia = body["user"]["id"]
    # Only super admins add admins; duplicates are rejected.
    assert client.post("/api/users", json={"email": "x@acme.io", "role": "admin"}, headers=CSRF).status_code == 403
    assert client.post("/api/users", json={"email": "new@acme.io"}, headers=CSRF).status_code == 422

    # The new user can't sign in yet; the link sets their password and signs them in.
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": "new@acme.io", "password": ""}).status_code in (401, 422)
    assert client.get(f"/api/auth/invite?token={token}").json() == {"email": "new@acme.io", "name": "Nia"}
    assert client.post("/api/auth/invite", json={"token": token, "password": "short"}).status_code == 422
    assert client.post("/api/auth/invite", json={"token": token, "password": PASSWORD}).status_code == 200
    me = client.get("/api/auth/me").json()["user"]
    assert me["email"] == "new@acme.io" and me["teams"] == ["search"]
    assert client.post("/api/auth/invite", json={"token": token, "password": PASSWORD}).status_code == 404  # used

    # Back as admin: change role, reset link, last-admin and self guards.
    client.cookies.clear()
    client.post("/api/auth/login", json={"email": "ada@acme.io", "password": PASSWORD})
    ada = client.get("/api/auth/me").json()["user"]["id"]
    upd = client.patch(f"/api/users/{nia}", json={"role": "member", "teams": None}, headers=CSRF).json()["user"]
    assert upd["role"] == "member" and upd["teams"] is None
    assert client.patch(f"/api/users/{ada}", json={"role": "viewer"}, headers=CSRF).status_code == 409
    assert client.patch(f"/api/users/{ada}", json={"disabled": True}, headers=CSRF).status_code == 409
    assert client.patch(f"/api/users/{nia}", json={"teams": []}, headers=CSRF).status_code == 422
    reset = client.post(f"/api/users/{nia}/invite", headers=CSRF).json()
    assert reset["invite_path"].startswith("/invite?token=") and reset["invite_path"] != body["invite_path"]
    assert client.patch(f"/api/users/{nia}", json={"disabled": True}, headers=CSRF).json()["user"]["disabled"]
    assert client.post(f"/api/users/{nia}/invite", headers=CSRF).status_code == 409


def test_last_admin_cannot_be_removed_by_another_admin(client, session):
    auth.create_user(session, "solo@acme.io", PASSWORD, role="admin")
    # Token access acts as admin without being a user; it still may not remove the only admin.
    solo = auth.find_user(session, "solo@acme.io")
    assert client.patch(f"/api/users/{solo.id}", json={"role": "member"}, headers=ADMIN_TOKEN).status_code == 409
    assert client.patch(f"/api/users/{solo.id}", json={"disabled": True}, headers=ADMIN_TOKEN).status_code == 409


def test_keys_from_the_dashboard(client, session):
    created = client.post("/api/keys", json={"name": "mobile backend"}, headers=ADMIN_TOKEN).json()
    secret = created["secret"]
    assert secret.startswith("br_live_") and created["key"]["prefix"] == secret[:12]
    listed = client.get("/api/keys", headers=ADMIN_TOKEN).json()["items"]
    assert all("secret" not in k and "key_hash" not in k for k in listed)
    ingest = {"events": [{"provider": "openai", "model": "gpt-4o", "input_tokens": 1}]}
    assert client.post("/v1/events", json=ingest, headers={"X-API-Key": secret}).status_code == 202
    assert client.post(f"/api/keys/{created['key']['id']}/revoke", headers=ADMIN_TOKEN).json()["key"]["revoked_at"]
    assert client.post("/v1/events", json=ingest, headers={"X-API-Key": secret}).status_code == 401


def test_only_revoked_keys_can_be_deleted(client, session):
    key = client.post("/api/keys", json={"name": "old app"}, headers=ADMIN_TOKEN).json()["key"]
    assert client.delete(f"/api/keys/{key['id']}", headers=ADMIN_TOKEN).status_code == 409  # still active
    client.post(f"/api/keys/{key['id']}/revoke", headers=ADMIN_TOKEN)
    assert client.delete(f"/api/keys/{key['id']}", headers=ADMIN_TOKEN).status_code == 204
    assert client.get("/api/keys", headers=ADMIN_TOKEN).json()["items"] == []
    assert client.delete(f"/api/keys/{key['id']}", headers=ADMIN_TOKEN).status_code == 404


def test_only_super_admins_manage_admins(client, session):
    _sign_in_as(client, session, "root@acme.io", role="superadmin")
    me = client.get("/api/auth/me").json()["user"]
    assert me["role"] == "superadmin"
    # Super admins add admins (never team-limited) and other super admins.
    assert client.post("/api/users", json={"email": "x@acme.io", "role": "admin", "teams": ["a"]},
                       headers=CSRF).status_code == 422
    ada = client.post("/api/users", json={"email": "ada@acme.io", "role": "admin"}, headers=CSRF).json()["user"]
    bo = client.post("/api/users", json={"email": "bo@acme.io", "role": "admin"}, headers=CSRF).json()["user"]
    assert client.post("/api/users", json={"email": "cy@acme.io", "role": "superadmin"},
                       headers=CSRF).status_code == 201
    member = client.post("/api/users", json={"email": "mo@acme.io", "role": "member"}, headers=CSRF).json()["user"]
    root_id = me["id"]

    # Ada (admin) still runs Settings for members and viewers ...
    auth.set_password(session, auth.find_user(session, "ada@acme.io"), PASSWORD)
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": "ada@acme.io", "password": PASSWORD}).status_code == 200
    assert client.get("/api/users").status_code == 200 and client.get("/api/keys").status_code == 200
    assert client.patch(f"/api/users/{member['id']}", json={"role": "viewer"}, headers=CSRF).status_code == 200
    assert client.post(f"/api/users/{member['id']}/invite", headers=CSRF).status_code == 200
    assert client.patch(f"/api/users/{ada['id']}", json={"name": "Ada L"}, headers=CSRF).status_code == 200
    # ... but can't add, promote, demote, disable or reset any admin or super admin.
    for role in ("admin", "superadmin"):
        assert client.post("/api/users", json={"email": f"{role}@acme.io", "role": role},
                           headers=CSRF).status_code == 403
        assert client.patch(f"/api/users/{member['id']}", json={"role": role}, headers=CSRF).status_code == 403
    for target in (bo["id"], root_id):
        assert client.patch(f"/api/users/{target}", json={"role": "viewer"}, headers=CSRF).status_code == 403
        assert client.patch(f"/api/users/{target}", json={"disabled": True}, headers=CSRF).status_code == 403
        assert client.post(f"/api/users/{target}/invite", headers=CSRF).status_code == 403
    assert client.patch(f"/api/users/{ada['id']}", json={"role": "superadmin"}, headers=CSRF).status_code == 409


def test_last_super_admin_stays(client, session):
    root = auth.create_user(session, "root@acme.io", PASSWORD, role="superadmin")
    auth.create_user(session, "ada@acme.io", PASSWORD, role="admin")
    # Token access acts as a super admin, and still may not remove the only one.
    assert client.patch(f"/api/users/{root.id}", json={"role": "admin"}, headers=ADMIN_TOKEN).status_code == 409
    assert client.patch(f"/api/users/{root.id}", json={"disabled": True}, headers=ADMIN_TOKEN).status_code == 409
    second = auth.find_user(session, "ada@acme.io")
    assert client.patch(f"/api/users/{second.id}", json={"role": "superadmin"},
                        headers=ADMIN_TOKEN).status_code == 200
    assert client.patch(f"/api/users/{root.id}", json={"role": "admin"}, headers=ADMIN_TOKEN).status_code == 200


def test_cli_first_user_is_super_admin_and_set_role(session, engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from burnrate import cli

    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "sync_registry", lambda s: None)
    monkeypatch.setattr(cli, "_read_password", lambda _stdin: PASSWORD)
    assert cli.main(["create-user", "first@acme.io"]) == 0
    assert cli.main(["create-user", "second@acme.io"]) == 0
    session.expire_all()
    assert auth.find_user(session, "first@acme.io").role == "superadmin"
    assert auth.find_user(session, "second@acme.io").role == "admin"
    assert cli.main(["set-role", "first@acme.io", "admin"]) == 1  # the only super admin
    assert cli.main(["set-role", "second@acme.io", "superadmin"]) == 0
    assert cli.main(["set-role", "first@acme.io", "viewer"]) == 0
    session.expire_all()
    assert [auth.find_user(session, e).role for e in ("first@acme.io", "second@acme.io")] == ["viewer", "superadmin"]


def test_editing_an_admin_keeps_them_counted(client, session):
    ada = auth.create_user(session, "ada@acme.io", PASSWORD, role="admin")
    assert client.patch(f"/api/users/{ada.id}", json={"name": "Ada"}, headers=ADMIN_TOKEN).status_code == 200
    session.expire_all()
    assert auth.active_admins(session) == 1
