"""Evaluate budget and anomaly conditions, persist new alerts, and deliver them to a webhook.

Run periodically (`burnrate alerts`, e.g. from cron every 15 minutes). Dedupe keys make it safe
to run as often as you like: a budget alerts once per month per status, an anomaly once per day.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate.config import get_settings
from burnrate.models import Alert
from burnrate.services import anomalies, budgets
from burnrate.services.analytics import today

ANOMALY_LOOKBACK_DAYS = 2
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


def evaluate(session: Session, client: httpx.Client | None = None) -> list[Alert]:
    """Persist alerts not seen before and deliver them. Returns the newly fired alerts."""
    candidates = _candidates(session)
    if not candidates:
        return []
    seen = set(session.scalars(select(Alert.dedupe_key).where(
        Alert.dedupe_key.in_([c.dedupe_key for c in candidates]))))
    fresh = [c for c in candidates if c.dedupe_key not in seen]
    session.add_all(fresh)
    session.commit()

    url = get_settings().alert_webhook_url
    if url and fresh:
        with client or httpx.Client(timeout=10) as http:
            for alert in fresh:
                try:
                    resp = http.post(url, json={"text": f"{EMOJI[alert.severity]} *{alert.title}*\n{alert.detail}"})
                    resp.raise_for_status()
                    alert.delivered_at = datetime.now(UTC)
                except httpx.HTTPError as e:
                    alert.delivery_error = str(e)[:500]
        session.commit()
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
