"""Aggregations behind the dashboard. All costs come from EventCost joined to raw UsageEvent."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import BigInteger, Select, cast, func, literal_column, select
from sqlalchemy.orm import Session

from burnrate.models import EventCost, UsageEvent

DIMENSIONS = {
    "provider": UsageEvent.provider,
    "model": UsageEvent.model,
    "project": UsageEvent.project,
    "feature": UsageEvent.feature,
    "team": UsageEvent.team,
    "environment": UsageEvent.environment,
    "customer": UsageEvent.customer_id,
    "user": UsageEvent.user_id,
}

COST = func.coalesce(func.sum(EventCost.total_cost_usd), 0.0)
# Cast before adding: four int4 columns can overflow int4 on Postgres.
TOKENS = func.coalesce(
    func.sum(
        cast(UsageEvent.input_tokens, BigInteger)
        + cast(UsageEvent.cached_input_tokens, BigInteger)
        + cast(UsageEvent.cache_write_tokens, BigInteger)
        + cast(UsageEvent.output_tokens, BigInteger)
    ),
    0,
)
REQUESTS = func.count(UsageEvent.id)
# Rendered inline (not a bound param) so Postgres sees identical SELECT and GROUP BY expressions.
UNATTRIBUTED = literal_column("'(unattributed)'")


def today() -> date:
    return datetime.now(UTC).date()


@dataclass(frozen=True)
class Window:
    start: date  # inclusive
    end: date  # inclusive

    @classmethod
    def last(cls, days: int, end: date | None = None) -> Window:
        end = end or today()
        return cls(end - timedelta(days=days - 1), end)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Window:
        return Window(self.start - timedelta(days=self.days), self.start - timedelta(days=1))

    def dates(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range(self.days)]


def _base(*cols) -> Select:
    return select(*cols).select_from(UsageEvent).join(EventCost, EventCost.event_id == UsageEvent.id)


def _in(stmt: Select, w: Window) -> Select:
    return stmt.where(UsageEvent.day >= w.start, UsageEvent.day <= w.end)


def _as_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def _apply_filters(stmt: Select, filters: dict[str, str | None]) -> Select:
    for dim, value in filters.items():
        if value:
            stmt = stmt.where(DIMENSIONS[dim] == value)
    return stmt


def daily_totals(session: Session, w: Window, filters: dict | None = None) -> dict[date, tuple[float, int, int]]:
    stmt = _in(_base(UsageEvent.day, COST, TOKENS, REQUESTS), w).group_by(UsageEvent.day)
    stmt = _apply_filters(stmt, filters or {})
    return {_as_date(d): (float(c), int(t), int(r)) for d, c, t, r in session.execute(stmt)}


def total_cost(session: Session, w: Window, filters: dict | None = None) -> float:
    stmt = _apply_filters(_in(_base(COST), w), filters or {})
    return float(session.scalar(stmt) or 0.0)


def month_window(on: date | None = None) -> Window:
    on = on or today()
    return Window(on.replace(day=1), on)


def days_in_month(on: date) -> int:
    return calendar.monthrange(on.year, on.month)[1]


def naive_projection(spent: float, on: date) -> float:
    elapsed = on.day
    return spent / elapsed * days_in_month(on) if elapsed else spent


def overview(session: Session, days: int) -> dict:
    w = Window.last(days)
    daily = daily_totals(session, w)
    total = sum(v[0] for v in daily.values())

    by_provider = session.execute(
        _in(_base(UsageEvent.provider, COST, TOKENS, REQUESTS), w)
        .group_by(UsageEvent.provider)
        .order_by(COST.desc())
    ).all()

    from burnrate.services.forecast import forecast_month  # local import avoids a cycle

    fc = forecast_month(session)
    return {
        "period": {"start": w.start.isoformat(), "end": w.end.isoformat()},
        "total_cost_usd": round(total, 4),
        "total_tokens": sum(v[1] for v in daily.values()),
        "request_count": sum(v[2] for v in daily.values()),
        "prev_period_cost_usd": round(total_cost(session, w.previous()), 4),
        "month_to_date_cost_usd": fc["month_to_date_usd"],
        "projected_month_cost_usd": fc["projected_usd"],
        "by_provider": [
            {"key": k, "cost_usd": round(float(c), 4), "tokens": int(t), "requests": int(r)}
            for k, c, t, r in by_provider
        ],
        "daily": [
            {
                "date": d.isoformat(),
                "cost_usd": round(daily.get(d, (0.0, 0, 0))[0], 4),
                "tokens": daily.get(d, (0.0, 0, 0))[1],
                "requests": daily.get(d, (0.0, 0, 0))[2],
            }
            for d in w.dates()
        ],
    }


def _grouped(session: Session, dim: str, w: Window) -> dict[str, tuple[float, int, int]]:
    col = DIMENSIONS[dim]
    key = func.coalesce(col, UNATTRIBUTED)
    stmt = _in(_base(key, COST, TOKENS, REQUESTS), w).group_by(key)
    return {k: (float(c), int(t), int(r)) for k, c, t, r in session.execute(stmt)}


def breakdown(session: Session, dimension: str, days: int, limit: int = 10) -> dict:
    w = Window.last(days)
    current = _grouped(session, dimension, w)
    previous = _grouped(session, dimension, w.previous())
    grand = sum(v[0] for v in current.values()) or 1.0
    ranked = sorted(current.items(), key=lambda kv: kv[1][0], reverse=True)

    def item(k: str, v: tuple[float, int, int]) -> dict:
        prev = previous.get(k, (0.0, 0, 0))[0]
        return {
            "key": k,
            "cost_usd": round(v[0], 4),
            "tokens": v[1],
            "requests": v[2],
            "share": round(v[0] / grand, 4),
            "change_pct": round((v[0] - prev) / prev * 100, 1) if prev > 0 else None,
        }

    items = [item(k, v) for k, v in ranked[:limit]]
    rest = ranked[limit:]
    if rest:
        agg = (sum(v[0] for _, v in rest), sum(v[1] for _, v in rest), sum(v[2] for _, v in rest))
        other = item("(other)", agg)
        other["change_pct"] = None
        items.append(other)
    return {"dimension": dimension, "items": items}


def timeseries(session: Session, group_by: str, days: int, top: int = 6) -> dict:
    w = Window.last(days)
    col = func.coalesce(DIMENSIONS[group_by], UNATTRIBUTED)
    rows = session.execute(_in(_base(UsageEvent.day, col, COST), w).group_by(UsageEvent.day, col)).all()

    totals: dict[str, float] = {}
    for _, k, c in rows:
        totals[k] = totals.get(k, 0.0) + float(c)
    keys = [k for k, _ in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)]
    shown, folded = keys[:top], set(keys[top:])
    series_keys = shown + (["(other)"] if folded else [])

    grid: dict[date, dict[str, float]] = {d: dict.fromkeys(series_keys, 0.0) for d in w.dates()}
    for d, k, c in rows:
        bucket = "(other)" if k in folded else k
        grid[_as_date(d)][bucket] += float(c)
    return {
        "group_by": group_by,
        "series_keys": series_keys,
        "points": [
            {"date": d.isoformat(), "values": {k: round(v, 4) for k, v in vals.items()}}
            for d, vals in grid.items()
        ],
    }


def list_events(session: Session, limit: int, offset: int, filters: dict[str, str | None]) -> dict:
    stmt = _apply_filters(
        select(UsageEvent, EventCost.total_cost_usd).join(EventCost, EventCost.event_id == UsageEvent.id),
        filters,
    )
    count_stmt = _apply_filters(select(func.count(UsageEvent.id)), filters)
    total = session.scalar(count_stmt) or 0
    rows = session.execute(
        stmt.order_by(UsageEvent.timestamp.desc(), UsageEvent.id.desc()).limit(limit).offset(offset)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": e.id,
                "request_id": e.request_id,
                "timestamp": e.timestamp.isoformat(),
                "provider": e.provider,
                "model": e.model,
                "project": e.project,
                "feature": e.feature,
                "team": e.team,
                "environment": e.environment,
                "customer_id": e.customer_id,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "cached_input_tokens": e.cached_input_tokens,
                "reasoning_tokens": e.reasoning_tokens,
                "cost_usd": round(cost, 6),
                "latency_ms": e.latency_ms,
                "status": e.status,
            }
            for e, cost in rows
        ],
    }


def meta(session: Session) -> dict:
    def distinct(col) -> list[str]:
        return sorted(v for v in session.scalars(select(col).distinct()) if v)

    return {
        "providers": distinct(UsageEvent.provider),
        "models": distinct(UsageEvent.model),
        "projects": distinct(UsageEvent.project),
        "features": distinct(UsageEvent.feature),
        "teams": distinct(UsageEvent.team),
        "environments": distinct(UsageEvent.environment),
    }
