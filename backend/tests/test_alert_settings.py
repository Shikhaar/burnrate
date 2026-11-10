import httpx
import pytest

from burnrate.config import get_settings
from burnrate.services import alerts, app_settings, scheduler

ADMIN = {"Authorization": "Bearer admin-token"}
HOOK = "https://hooks.slack.com/services/T000/B000/abcdefghijkl"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(get_settings(), "dashboard_token", "admin-token")
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "")


@pytest.fixture
def slack(monkeypatch):
    """Fake Slack: records posts; answers with `status`."""
    state = {"posts": [], "status": 200}
    real = httpx.Client

    def handler(req: httpx.Request) -> httpx.Response:
        state["posts"].append((str(req.url), req.content.decode()))
        return httpx.Response(state["status"], text="ok" if state["status"] == 200 else "no_service")

    monkeypatch.setattr(alerts.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    return state


def test_connect_requires_a_working_https_webhook(client, slack):
    assert client.get("/api/settings/alerts", headers=ADMIN).json()["connected"] is False
    bad = client.put("/api/settings/alerts", json={"webhook_url": "http://hooks.slack.com/x"}, headers=ADMIN)
    assert bad.status_code == 422 and slack["posts"] == []

    slack["status"] = 404
    r = client.put("/api/settings/alerts", json={"webhook_url": HOOK}, headers=ADMIN)
    assert r.status_code == 400 and "doesn't exist" in r.json()["detail"]
    assert client.get("/api/settings/alerts", headers=ADMIN).json()["connected"] is False  # not saved

    slack["status"] = 200
    ok = client.put("/api/settings/alerts", json={"webhook_url": HOOK}, headers=ADMIN).json()
    assert ok["connected"] and ok["source"] == "dashboard" and ok["is_slack"]
    assert ok["webhook_hint"] == "https://hooks.slack.com/…ijkl" and "B000" not in ok["webhook_hint"]
    assert "Burnrate is connected" in slack["posts"][-1][1]

    assert client.post("/api/settings/alerts/test", headers=ADMIN).json() == {"sent": True}
    assert client.delete("/api/settings/alerts", headers=ADMIN).json()["connected"] is False
    assert client.post("/api/settings/alerts/test", headers=ADMIN).status_code == 409


def test_dashboard_webhook_wins_over_environment(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "https://env.example/hook")
    assert app_settings.webhook_url(session) == ("https://env.example/hook", "environment")
    app_settings.put(session, app_settings.WEBHOOK_KEY, HOOK)
    assert app_settings.webhook_url(session) == (HOOK, "dashboard")


def test_alerts_go_to_the_dashboard_webhook_and_timer_records_runs(session, engine, monkeypatch, slack):
    from sqlalchemy.orm import sessionmaker

    from burnrate import db
    from burnrate.models import Budget
    from burnrate.schemas import EventIn
    from burnrate.services.ingest import ingest_events

    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    app_settings.put(session, app_settings.WEBHOOK_KEY, HOOK)
    ingest_events(session, [EventIn(provider="anthropic", model="claude-sonnet-4-5", input_tokens=10_000_000)])
    session.add(Budget(name="Tiny", monthly_limit_usd=1.0))
    session.commit()

    assert scheduler.run_once() == 1
    url, body = slack["posts"][-1]
    assert url == HOOK and "Budget exceeded: Tiny" in body
    status = alerts.delivery_status(session)
    assert status["last_delivered_at"] and status["undelivered"] == 0
    assert app_settings.get(session, app_settings.LAST_RUN_KEY)
    assert scheduler.run_once() == 0  # deduplicated: the same condition never posts twice
    assert len(slack["posts"]) == 1


def test_settings_are_admin_only_and_timer_can_be_disabled(client, session):
    import asyncio

    from burnrate import auth

    auth.create_user(session, "mo@acme.io", "correct horse battery", role="member")
    get_settings().cookie_secure = False
    try:
        client.post("/api/auth/login", json={"email": "mo@acme.io", "password": "correct horse battery"})
        assert client.get("/api/settings/alerts").status_code == 403
    finally:
        get_settings().cookie_secure = True

    async def run():
        async with scheduler.alert_timer(0):
            return "no task started"

    assert asyncio.run(run()) == "no task started"
