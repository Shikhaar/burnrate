import base64
import json

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from burnrate.config import get_settings
from burnrate.models import Alert, AuditLog, Budget
from burnrate.schemas import EventIn
from burnrate.services import alerts, jira
from burnrate.services.ingest import ingest_events

ADMIN = {"Authorization": "Bearer admin-token"}
GOOD = {"site": "acme.atlassian.net", "email": "bot@acme.io", "api_token": "tok-1234567890",
        "project_key": "ops", "issue_type": "Task"}


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(get_settings(), "dashboard_token", "admin-token")
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "")


@pytest.fixture
def fake_jira(monkeypatch):
    state = {"issues": [], "fail_create": None, "next": 1}
    real = httpx.Client

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "acme.atlassian.net"
        user, _, token = base64.b64decode(req.headers["authorization"].split()[1]).decode().partition(":")
        if (user, token) != ("bot@acme.io", "tok-1234567890"):
            return httpx.Response(401, json={"errorMessages": ["unauthorized"]})
        path = req.url.path
        if path == "/rest/api/3/myself":
            return httpx.Response(200, json={"displayName": "Burnrate Bot"})
        if path.startswith("/rest/api/3/project/"):
            if path.endswith("/OPS"):
                return httpx.Response(200, json={"name": "Operations", "issueTypes": [
                    {"name": "Task"}, {"name": "Bug"}, {"name": "Sub-task", "subtask": True}]})
            return httpx.Response(404, json={"errorMessages": ["No project"]})
        if path == "/rest/api/3/issue" and req.method == "POST":
            if state["fail_create"]:
                return httpx.Response(state["fail_create"], json={"errors": {"summary": "too long"}})
            body = json.loads(req.content)
            state["issues"].append(body["fields"])
            key = f"OPS-{state['next']}"
            state["next"] += 1
            return httpx.Response(201, json={"key": key})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    return state


def test_connect_checks_login_project_and_issue_type(client, session, fake_jira):
    assert client.get("/api/settings/jira", headers=ADMIN).json()["connected"] is False

    bad = client.put("/api/settings/jira", json={**GOOD, "api_token": "wrong"}, headers=ADMIN)
    assert bad.status_code == 400 and "didn't accept" in bad.json()["detail"]
    nope = client.put("/api/settings/jira", json={**GOOD, "project_key": "XYZ"}, headers=ADMIN)
    assert nope.status_code == 400 and "project key" in nope.json()["detail"]
    kind = client.put("/api/settings/jira", json={**GOOD, "issue_type": "Story"}, headers=ADMIN)
    assert kind.status_code == 400 and "Try one of: Task, Bug" in kind.json()["detail"]
    assert client.put("/api/settings/jira", json={**GOOD, "site": "http://acme.atlassian.net"},
                      headers=ADMIN).status_code == 400
    assert fake_jira["issues"] == []  # checking never creates tickets

    ok = client.put("/api/settings/jira", json=GOOD, headers=ADMIN).json()
    assert ok["connected"] and ok["site"] == "https://acme.atlassian.net" and ok["project_key"] == "OPS"
    assert ok["verified"]["project_name"] == "Operations"
    assert ok["token_hint"] == "…7890" and "tok-1234567890" not in json.dumps(ok)

    # Editing without re-entering the token keeps it.
    edited = client.put("/api/settings/jira", json={**GOOD, "api_token": None, "issue_type": "Bug",
                                                    "min_severity": "medium"}, headers=ADMIN).json()
    assert edited["issue_type"] == "Bug" and edited["min_severity"] == "medium"
    # The audit trail says whether the token changed, never what it is.
    session.expire_all()
    tokens = [r.details["api_token"] for r in session.query(AuditLog)
              .filter(AuditLog.action == "settings.jira.update").order_by(AuditLog.id)]
    assert tokens == ["changed", "unchanged"]

    test = client.post("/api/settings/jira/test", headers=ADMIN).json()
    assert test == {"key": "OPS-1", "url": "https://acme.atlassian.net/browse/OPS-1"}
    assert "burnrate-test" in fake_jira["issues"][0]["labels"]

    assert client.delete("/api/settings/jira", headers=ADMIN).json()["connected"] is False
    assert client.post("/api/settings/jira/test", headers=ADMIN).status_code == 409


def _connect(session, **kw):
    jira.save(session, jira.JiraConfig(site="https://acme.atlassian.net", email="bot@acme.io",
                                       api_token="tok-1234567890", project_key="OPS", issue_type="Task", **kw))


def _two_budget_alerts(session):
    ingest_events(session, [EventIn(provider="anthropic", model="claude-sonnet-4-5", input_tokens=10_000_000)])
    session.add_all([Budget(name="Tiny", monthly_limit_usd=1.0),  # exceeded -> high
                     Budget(name="Close", monthly_limit_usd=33.0, alert_threshold=0.5)])  # at risk -> medium
    session.commit()


def test_serious_alerts_become_one_ticket_each(session, fake_jira):
    _connect(session)
    _two_budget_alerts(session)
    fired = alerts.evaluate(session)
    assert {a.severity for a in fired} == {"high", "medium"}
    assert len(fake_jira["issues"]) == 1  # only "high" by default
    issue = fake_jira["issues"][0]
    assert issue["summary"] == "[Burnrate] Budget exceeded: Tiny"
    assert issue["project"] == {"key": "OPS"} and issue["issuetype"] == {"name": "Task"}
    assert set(issue["labels"]) == {"burnrate", "burnrate-budget"}
    assert "of $1.00 spent" in json.dumps(issue["description"])

    alerts.evaluate(session)  # runs again: nothing new
    assert len(fake_jira["issues"]) == 1
    history = {a["title"]: a for a in alerts.recent(session)}
    assert history["Budget exceeded: Tiny"]["jira_url"] == "https://acme.atlassian.net/browse/OPS-1"
    assert history["Budget at risk: Close"]["jira_issue_key"] is None


def test_medium_setting_and_failures_are_recorded(session, fake_jira):
    _connect(session, min_severity="medium")
    _two_budget_alerts(session)
    fake_jira["fail_create"] = 400
    alerts.evaluate(session)
    rows = session.query(Alert).all()
    assert all(a.jira_issue_key is None and a.jira_attempts == 1 for a in rows)
    assert "summary: too long" in jira.status(session)["last_failure"]["error"]

    fake_jira["fail_create"] = None
    alerts.evaluate(session)  # retried on the next check
    assert len(fake_jira["issues"]) == 2
    assert jira.status(session)["last_ticket"]["key"] in {"OPS-1", "OPS-2"}


def test_two_checks_at_once_create_one_ticket(session, engine, fake_jira):
    _connect(session)
    _two_budget_alerts(session)
    alerts.evaluate(session)  # creates OPS-1 for the high alert
    session.query(Alert).update({Alert.jira_issue_key: None, Alert.jira_attempts: 0})
    session.commit()
    fake_jira["issues"].clear()

    other = sessionmaker(bind=engine, expire_on_commit=False)()
    stale_a = session.query(Alert).all()
    stale_b = other.query(Alert).all()  # same rows, read before either delivers
    jira.deliver_pending(session, stale_a)
    jira.deliver_pending(other, stale_b)
    assert len(fake_jira["issues"]) == 1
    other.close()


def test_jira_settings_are_admin_only(client, session):
    from burnrate import auth

    auth.create_user(session, "mo@acme.io", "correct horse battery", role="member")
    get_settings().cookie_secure = False
    try:
        client.post("/api/auth/login", json={"email": "mo@acme.io", "password": "correct horse battery"})
        assert client.get("/api/settings/jira").status_code == 403
        assert client.put("/api/settings/jira", json=GOOD, headers={"X-Requested-With": "burnrate"}).status_code == 403
    finally:
        get_settings().cookie_secure = True
