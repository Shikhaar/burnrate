"""Evaluate budget and anomaly conditions, persist new alerts, and deliver them to a webhook.

The API runs this on its own every `BURNRATE_ALERT_INTERVAL_MINUTES` (default 15); `burnrate alerts`
runs it once, e.g. from cron when the built-in timer is off. Dedupe keys make it safe
to run as often as you like: a budget alerts once per month per status, an anomaly once per day.
Failed webhook deliveries are retried with backoff, and again on later runs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate.db import insert_ignoring_conflicts
from burnrate.metrics import ALERTS_FIRED, WEBHOOK_DELIVERIES
from burnrate.models import Alert
from burnrate.services import anomalies, app_settings, budgets
from burnrate.services.analytics import today

ANOMALY_LOOKBACK_DAYS = 2
ATTEMPTS_PER_RUN = 3
MAX_DELIVERY_ATTEMPTS = 5
BACKOFF_SECONDS = 0.5
RETRY_WINDOW = timedelta(hours=24)
NOTIFY_ANOMALY_SEVERITIES = {"high", "medium"}
EMOJI = {"high": ":rotating_light:", "medium": ":warning:", "low": ":information_source:"}


def _candidates(session: Session) -> list[Alert]:
    month = today().strftime("%Y-%m")
    out: list[Alert] = []
    for b in budgets.list_budgets(session):
        if b["status"] == "ok":
            continue
        scope = f"{b['scope_dimension']}={b['scope_value']}" if b["scope_dimension"] else "all spend"
        if b["status"] == "exceeded":
            title = f"Budget exceeded: {b['name']}"
            severity = "high"
        else:
            title = f"Budget at risk: {b['name']}"
            severity = "medium"
        out.append(Alert(
            dedupe_key=f"budget:{b['id']}:{month}:{b['status']}",
            kind="budget",
            severity=severity,
            title=title,
            detail=f"{scope}: ${b['spent_usd']:,.2f} of ${b['monthly_limit_usd']:,.2f} spent "
                   f"({b['utilization']:.0%}); projected ${b['projected_usd']:,.2f} by month end.",
        ))
    for a in anomalies.detect(session, days=ANOMALY_LOOKBACK_DAYS):
        if a["severity"] in NOTIFY_ANOMALY_SEVERITIES:
            related = f" Related: {', '.join(a['related'])}." if a["related"] else ""
            out.append(Alert(dedupe_key=f"anomaly:{a['id']}", kind="anomaly", severity=a["severity"],
                             title=f"Spend spike: {a['dimension']} {a['key']}",
                             detail=(a["message"] + related)[:1000]))
    return out


def _deliver(http: httpx.Client, url: str, alert: Alert, sleep: Callable[[float], None]) -> None:
    """POST one alert, retrying transient failures (network, 5xx, 429) with exponential backoff."""
    text = f"{EMOJI.get(alert.severity, '')} *{alert.title}*\n{alert.detail}".strip()
    for attempt in range(ATTEMPTS_PER_RUN):
        alert.delivery_attempts += 1
        try:
            resp = http.post(url, json={"text": text})
        except httpx.HTTPError as e:
            alert.delivery_error = f"{type(e).__name__}: {e}"[:500]
        else:
            if resp.is_success:
                alert.delivered_at = datetime.now(UTC)
                alert.delivery_error = None
                WEBHOOK_DELIVERIES.labels("delivered").inc()
                return
            alert.delivery_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code < 500 and resp.status_code != 429:
                break  # a bad URL or payload won't fix itself by retrying
        if attempt < ATTEMPTS_PER_RUN - 1 and alert.delivery_attempts < MAX_DELIVERY_ATTEMPTS:
            sleep(BACKOFF_SECONDS * 2**attempt)
        if alert.delivery_attempts >= MAX_DELIVERY_ATTEMPTS:
            break
    WEBHOOK_DELIVERIES.labels("failed").inc()


def evaluate(
    session: Session, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep
) -> list[Alert]:
    """Persist alerts not seen before, then deliver every undelivered alert that's still worth sending.

    Safe to run concurrently (cron + "Evaluate now"): inserts use ON CONFLICT DO NOTHING on the dedupe
    key, so a condition can only ever fire once. Alerts whose delivery failed are retried on later runs
    until they succeed, reach MAX_DELIVERY_ATTEMPTS, or are older than RETRY_WINDOW.
    """
    fresh: list[Alert] = []
    candidates = _candidates(session)
    if candidates:
        rows = [
            {"dedupe_key": c.dedupe_key, "kind": c.kind, "severity": c.severity, "title": c.title,
             "detail": c.detail, "created_at": datetime.now(UTC), "delivery_attempts": 0}
            for c in candidates
        ]
        stmt = insert_ignoring_conflicts(session, Alert, ["dedupe_key"]).returning(Alert.id)
        new_ids = [row.id for row in session.execute(stmt, rows)]
        session.commit()
        fresh = list(session.scalars(select(Alert).where(Alert.id.in_(new_ids)).order_by(Alert.id)))
        for a in fresh:
            ALERTS_FIRED.labels(a.kind, a.severity).inc()

    url, _ = app_settings.webhook_url(session)
    if url:
        pending = list(session.scalars(
            select(Alert)
            .where(Alert.delivered_at.is_(None),
                   Alert.delivery_attempts < MAX_DELIVERY_ATTEMPTS,
                   Alert.created_at >= datetime.now(UTC) - RETRY_WINDOW)
            .order_by(Alert.id)
        ))
        if pending:
            owns_client = client is None
            http = client or httpx.Client(timeout=10)
            try:
                for alert in pending:
                    _deliver(http, url, alert, sleep)
                    session.commit()  # record each outcome as we go
            finally:
                if owns_client:
                    http.close()
    return fresh


def recent(session: Session, days: int = 30) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.scalars(select(Alert).where(Alert.created_at >= since).order_by(Alert.created_at.desc()))
    return [
        {"id": a.id, "kind": a.kind, "severity": a.severity, "title": a.title, "detail": a.detail,
         "created_at": a.created_at.isoformat(), "delivered": a.delivered_at is not None,
         "delivery_error": a.delivery_error}
        for a in rows
    ]


TEST_MESSAGE = (":white_check_mark: *Burnrate is connected.* Budget and spend-spike alerts will be posted "
                "to this channel.")


def send_test(url: str, client: httpx.Client | None = None) -> str | None:
    """Post a test message. Returns None on success, or a short reason it failed."""
    owns = client is None
    http = client or httpx.Client(timeout=10)
    try:
        resp = http.post(url, json={"text": TEST_MESSAGE})
    except httpx.HTTPError as e:
        return f"Couldn't reach the webhook ({type(e).__name__})."
    finally:
        if owns:
            http.close()
    if resp.is_success:
        return None
    hint = {404: " Slack says this webhook doesn't exist; it may have been removed.",
            403: " Slack refused it; the app may have been uninstalled from the channel.",
            410: " Slack says this channel was archived or the webhook was revoked."}.get(resp.status_code, "")
    return f"The webhook answered HTTP {resp.status_code}.{hint}"


def delivery_status(session: Session) -> dict:
    """Most recent successful delivery and most recent failure, for the settings page."""
    last_ok = session.scalar(select(Alert.delivered_at).where(Alert.delivered_at.is_not(None))
                             .order_by(Alert.delivered_at.desc()).limit(1))
    failed = session.scalars(select(Alert).where(Alert.delivered_at.is_(None), Alert.delivery_error.is_not(None))
                             .order_by(Alert.created_at.desc()).limit(1)).first()
    return {
        "last_delivered_at": last_ok.isoformat() if last_ok else None,
        "last_failure": ({"at": failed.created_at.isoformat(), "error": failed.delivery_error, "title": failed.title}
                         if failed else None),
        "undelivered": session.query(Alert).filter(Alert.delivered_at.is_(None)).count(),
    }
