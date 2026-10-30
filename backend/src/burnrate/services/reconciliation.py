"""Estimated (token x price) vs billed (provider invoice/cost API) reconciliation."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from burnrate.models import BilledCost, UsageEvent
from burnrate.services.analytics import COST, Window, _as_date, _base, _in

# Deltas inside this band are normal (rounding, billing-day boundaries, unlogged retries).
TOLERANCE_PCT = 2.0


def reconcile(session: Session, days: int) -> dict:
    w = Window.last(days)
    est_rows = session.execute(
        _in(_base(UsageEvent.day, UsageEvent.provider, COST), w).group_by(UsageEvent.day, UsageEvent.provider)
    ).all()
    billed_rows = session.execute(
        select(BilledCost.day, BilledCost.provider, func.sum(BilledCost.amount_usd))
        .where(BilledCost.day >= w.start, BilledCost.day <= w.end)
        .group_by(BilledCost.day, BilledCost.provider)
    ).all()

    cells: dict[tuple, dict] = {}
    for d, p, c in est_rows:
        cells.setdefault((_as_date(d), p), {"estimated_usd": 0.0, "billed_usd": None})["estimated_usd"] = float(c)
    for d, p, c in billed_rows:
        cells.setdefault((_as_date(d), p), {"estimated_usd": 0.0, "billed_usd": None})["billed_usd"] = float(c)

    per_provider: dict[str, dict] = {}
    for (_, p), v in cells.items():
        agg = per_provider.setdefault(p, {"estimated_usd": 0.0, "billed_usd": 0.0, "has_billing": False})
        if v["billed_usd"] is not None:
            # Compare like with like: only days the provider has billed.
            agg["estimated_usd"] += v["estimated_usd"]
            agg["billed_usd"] += v["billed_usd"]
            agg["has_billing"] = True

    items = []
    for p, agg in sorted(per_provider.items(), key=lambda kv: -kv[1]["billed_usd"]):
        if not agg["has_billing"]:
            items.append({"provider": p, "estimated_usd": None, "billed_usd": None, "delta_usd": None,
                          "delta_pct": None, "status": "no_billing_data"})
            continue
        delta = agg["billed_usd"] - agg["estimated_usd"]
        pct = delta / agg["estimated_usd"] * 100 if agg["estimated_usd"] else None
        items.append(
            {
                "provider": p,
                "estimated_usd": round(agg["estimated_usd"], 2),
                "billed_usd": round(agg["billed_usd"], 2),
                "delta_usd": round(delta, 2),
                "delta_pct": round(pct, 2) if pct is not None else None,
                "status": "ok" if pct is not None and abs(pct) <= TOLERANCE_PCT else "investigate",
            }
        )

    return {
        "tolerance_pct": TOLERANCE_PCT,
        "items": items,
        "daily": [
            {"date": d.isoformat(), "provider": p, "estimated_usd": round(v["estimated_usd"], 4),
             "billed_usd": round(v["billed_usd"], 4) if v["billed_usd"] is not None else None}
            for (d, p), v in sorted(cells.items())
        ],
    }
