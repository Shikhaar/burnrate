"""Spend anomaly detection.

For each (dimension, key) series we compare a day's spend with a trailing 14-day baseline using a
robust z-score (median / MAD), so one earlier spike doesn't inflate the baseline. A day is flagged
when it is both statistically unusual and materially large, to avoid paging on $0.40 blips.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from burnrate.models import UsageEvent
from burnrate.services.analytics import COST, DIMENSIONS, UNATTRIBUTED, Window, _as_date, _base, _in

BASELINE_DAYS = 14
MIN_Z = 3.5
MIN_CHANGE_PCT = 50.0
MIN_DELTA_USD = 5.0
WATCHED = ("project", "feature", "model")


def _severity(z: float, change_pct: float) -> str:
    if z >= 8 or change_pct >= 200:
        return "high"
    if z >= 5 or change_pct >= 100:
        return "medium"
    return "low"


def detect(session: Session, days: int = 30, dimensions: tuple[str, ...] = WATCHED) -> list[dict]:
    w = Window.last(days)
    full = Window(w.start - timedelta(days=BASELINE_DAYS), w.end)
    found: list[dict] = []

    for dim in dimensions:
        col = func.coalesce(DIMENSIONS[dim], UNATTRIBUTED)
        rows = session.execute(_in(_base(UsageEvent.day, col, COST), full).group_by(UsageEvent.day, col))
        series: dict[str, dict[date, float]] = {}
        for d, k, c in rows:
            series.setdefault(k, {})[_as_date(d)] = float(c)

        for key, by_day in series.items():
            for d in w.dates():
                baseline = [by_day.get(d - timedelta(days=i), 0.0) for i in range(1, BASELINE_DAYS + 1)]
                if sum(1 for b in baseline if b > 0) < BASELINE_DAYS // 2:
                    continue  # not enough history to judge
                value = by_day.get(d, 0.0)
                med = statistics.median(baseline)
                mad = statistics.median(abs(b - med) for b in baseline) * 1.4826
                spread = max(mad, med * 0.05, 0.01)
                z = (value - med) / spread
                change = (value - med) / med * 100 if med > 0 else 0.0
                if z >= MIN_Z and change >= MIN_CHANGE_PCT and value - med >= MIN_DELTA_USD:
                    found.append(
                        {
                            "id": f"{dim}:{key}:{d.isoformat()}",
                            "date": d.isoformat(),
                            "dimension": dim,
                            "key": key,
                            "cost_usd": round(value, 2),
                            "baseline_usd": round(med, 2),
                            "z_score": round(z, 1),
                            "change_pct": round(change, 1),
                            "severity": _severity(z, change),
                            "message": f"{dim.capitalize()} '{key}' spent ${value:,.2f} on {d.isoformat()}, "
                            f"{change:+.0f}% vs its {BASELINE_DAYS}-day median of ${med:,.2f}.",
                        }
                    )

    order = {"high": 0, "medium": 1, "low": 2}
    found.sort(key=lambda a: (a["date"], -order[a["severity"]], a["cost_usd"]), reverse=True)
    return found

