"""Traces: the calls of one agent run or request chain, grouped by `trace_id`, with what each step cost.

Burnrate keeps no prompts or responses, so a trace here is about money and time: which steps ran, in
what order, on which model, how long each took, what each cost, and how good the result was (scores
from Langfuse or sent by an app). For the prompts themselves, each trace links to the tool that has
them (Langfuse, Phoenix, ...).

Team scoping applies as everywhere else: the usage-event queries below are filtered to the caller's
teams by the session listener in `burnrate.auth`, and scores are only ever read through those events.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from burnrate.config import get_settings
from burnrate.db import insert_ignoring_conflicts
from burnrate.models import EventCost, TraceScore, UsageEvent
from burnrate.schemas import IngestResult, ScoreIn
from burnrate.services.analytics import COST, DIMENSIONS, TOKENS, Window

SORTS = ("cost", "recent", "calls")
TRACE_FILTERS = ("project", "feature", "customer", "team")


# ---- queries ---------------------------------------------------------------------------------------------
def _in_window(stmt, w: Window):
    return stmt.where(UsageEvent.trace_id.isnot(None), UsageEvent.day >= w.start, UsageEvent.day <= w.end)


def _traces(w: Window, filters: dict[str, str | None]):
    """One row per trace in the window: totals, a name, and its first step's project and customer.

    The name is the step name (feature) when every call in the trace shares it, as Langfuse traces do;
    otherwise the trace spans several steps and is named after its project.
    """
    errors = func.sum(case((UsageEvent.status != "success", 1), else_=0))
    per_trace = _in_window(
        select(UsageEvent.trace_id.label("trace_id"), func.min(UsageEvent.timestamp).label("started"),
               func.count(UsageEvent.id).label("calls"), COST.label("cost"), TOKENS.label("tokens"),
               errors.label("errors"), func.count(func.distinct(UsageEvent.feature)).label("features"),
               func.min(UsageEvent.feature).label("only_feature"))
        .select_from(UsageEvent).join(EventCost, EventCost.event_id == UsageEvent.id),
        w,
    )
    for dim, value in filters.items():
        if value:  # a trace matches if any of its steps does
            per_trace = per_trace.where(UsageEvent.trace_id.in_(
                _in_window(select(UsageEvent.trace_id), w).where(DIMENSIONS[dim] == value)))
    per_trace = per_trace.group_by(UsageEvent.trace_id).subquery()

    ranked = _in_window(
        select(UsageEvent.trace_id, UsageEvent.project, UsageEvent.feature, UsageEvent.customer_id,
               UsageEvent.user_id,
               func.row_number().over(partition_by=UsageEvent.trace_id,
                                      order_by=(UsageEvent.timestamp, UsageEvent.id)).label("rn")),
        w,
    ).subquery()
    first = select(ranked).where(ranked.c.rn == 1).subquery()
    name = case((per_trace.c.features == 1, per_trace.c.only_feature), else_=first.c.project).label("name")
    return (
        select(per_trace, name, first.c.project, first.c.customer_id, first.c.user_id)
        .join(first, first.c.trace_id == per_trace.c.trace_id)
    ).subquery()


def _score_summary(session: Session, trace_ids: list[str]) -> dict[str, dict[str, float | str]]:
    """Per trace: the average of each numeric/boolean score, or the latest label of a categorical one."""
    out: dict[str, dict[str, float | str]] = defaultdict(dict)
    if not trace_ids:
        return out
    rows = session.execute(
        select(TraceScore.trace_id, TraceScore.name, TraceScore.value, TraceScore.label)
        .where(TraceScore.trace_id.in_(trace_ids)).order_by(TraceScore.timestamp, TraceScore.id)
    ).all()
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for trace_id, name, value, label in rows:
        if value is not None:
            values[(trace_id, name)].append(value)
        elif label:
            out[trace_id][name] = label
    for (trace_id, name), vals in values.items():
        out[trace_id][name] = round(sum(vals) / len(vals), 4)
    return out


def trace_link(trace_id: str, attributes: list[dict]) -> str | None:
    """Where to see this trace's prompts: the URL an importer recorded, else the configured template."""
    for attrs in attributes:
        if isinstance(attrs, dict) and attrs.get("trace_url"):
            return str(attrs["trace_url"])
    template = get_settings().trace_url_template
    return template.replace("{trace_id}", trace_id) if template else None


def _duration_ms(events: list[tuple[datetime, int | None]]) -> int:
    starts = [ts for ts, _ in events]
    ends = [ts + timedelta(milliseconds=lat or 0) for ts, lat in events]
    return int((max(ends) - min(starts)).total_seconds() * 1000) if events else 0


def _iso(ts: datetime) -> str:
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).isoformat()


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


# ---- list ------------------------------------------------------------------------------------------------
def list_traces(session: Session, days: int, limit: int = 50, offset: int = 0, sort: str = "cost",
                filters: dict[str, str | None] | None = None, errors_only: bool = False) -> dict:
    w = Window.last(days)
    t = _traces(w, filters or {})
    base = select(t)
    if errors_only:
        base = base.where(t.c.errors > 0)
    base = base.subquery()

    totals = session.execute(select(func.count(), func.coalesce(func.sum(base.c.cost), 0.0),
                                    func.coalesce(func.sum(base.c.calls), 0)).select_from(base)).one()
    order = {"cost": base.c.cost.desc(), "recent": base.c.started.desc(), "calls": base.c.calls.desc()}[sort]
    page = session.execute(select(base).order_by(order, base.c.trace_id).limit(limit).offset(offset)).all()

    ids = [r.trace_id for r in page]
    steps: dict[str, list] = defaultdict(list)
    if ids:
        for e in session.execute(
            select(UsageEvent.trace_id, UsageEvent.timestamp, UsageEvent.latency_ms, UsageEvent.model,
                   UsageEvent.feature, UsageEvent.attributes)
            .where(UsageEvent.trace_id.in_(ids)).order_by(UsageEvent.timestamp, UsageEvent.id)
        ).all():
            steps[e.trace_id].append(e)
    scores = _score_summary(session, ids)

    workflows = session.execute(
        select(base.c.name, func.count().label("traces"), func.sum(base.c.cost).label("cost"),
               func.avg(base.c.calls).label("avg_calls"),
               func.sum(case((base.c.errors > 0, 1), else_=0)).label("failed"))
        .group_by(base.c.name).order_by(func.sum(base.c.cost).desc()).limit(20)
    ).all()
    workflow_scores = _workflow_scores(session, base)

    count, cost, calls = totals
    return {
        "window": {"start": w.start.isoformat(), "end": w.end.isoformat(), "days": w.days},
        "summary": {
            "traces": count,
            "cost_usd": round(cost, 4),
            "avg_cost_usd": round(cost / count, 6) if count else 0.0,
            "avg_calls": round(calls / count, 2) if count else 0.0,
        },
        "workflows": [
            {"name": r.name or "(unnamed)", "traces": r.traces, "cost_usd": round(r.cost, 4),
             "avg_cost_usd": round(r.cost / r.traces, 6), "avg_calls": round(float(r.avg_calls), 2),
             "failed_share": round(r.failed / r.traces, 4), "scores": workflow_scores.get(r.name, {})}
            for r in workflows
        ],
        "total": count,
        "items": [
            {
                "trace_id": r.trace_id,
                "name": r.name or "(unnamed)",
                "project": r.project,
                "customer_id": r.customer_id,
                "user_id": r.user_id,
                "started_at": _iso(r.started),
                "duration_ms": _duration_ms([(_aware(s.timestamp), s.latency_ms) for s in steps[r.trace_id]]),
                "calls": r.calls,
                "cost_usd": round(r.cost, 6),
                "tokens": int(r.tokens),
                "errors": int(r.errors),
                "models": list(dict.fromkeys(s.model for s in steps[r.trace_id])),
                "steps": [s.feature or s.model for s in steps[r.trace_id]][:12],
                "scores": scores.get(r.trace_id, {}),
                "link": trace_link(r.trace_id, [s.attributes for s in steps[r.trace_id]]),
            }
            for r in page
        ],
    }


def _workflow_scores(session: Session, traces) -> dict[str, dict[str, float]]:
    """Average numeric/boolean score per workflow (trace name), across that workflow's scored traces."""
    per_trace = (
        select(TraceScore.trace_id, TraceScore.name.label("score"), func.avg(TraceScore.value).label("value"))
        .where(TraceScore.value.isnot(None)).group_by(TraceScore.trace_id, TraceScore.name).subquery()
    )
    rows = session.execute(
        select(traces.c.name, per_trace.c.score, func.avg(per_trace.c.value), func.count())
        .join(per_trace, per_trace.c.trace_id == traces.c.trace_id)
        .group_by(traces.c.name, per_trace.c.score)
    ).all()
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for name, score, value, _n in rows:
        out[name][score] = round(float(value), 4)
    return out


# ---- detail ----------------------------------------------------------------------------------------------
def get_trace(session: Session, trace_id: str) -> dict | None:
    rows = session.execute(
        select(UsageEvent, EventCost.total_cost_usd, EventCost.method)
        .join(EventCost, EventCost.event_id == UsageEvent.id)
        .where(UsageEvent.trace_id == trace_id).order_by(UsageEvent.timestamp, UsageEvent.id)
    ).all()
    if not rows:
        return None  # unknown, or only other teams' calls
    start = _aware(rows[0][0].timestamp)
    total = sum(cost for _, cost, _ in rows) or 0.0
    steps, prev = [], None
    for e, cost, method in rows:
        ts = _aware(e.timestamp)
        steps.append({
            "id": e.id,
            "request_id": e.request_id,
            "timestamp": ts.isoformat(),
            "offset_ms": int((ts - start).total_seconds() * 1000),
            "latency_ms": e.latency_ms,
            "provider": e.provider,
            "model": e.model,
            "operation": e.operation,
            "feature": e.feature,
            "input_tokens": e.input_tokens,
            "cached_input_tokens": e.cached_input_tokens,
            "cache_write_tokens": e.cache_write_tokens,
            "output_tokens": e.output_tokens,
            "cost_usd": round(cost, 6),
            "cost_share": round(cost / total, 4) if total else 0.0,
            "pricing": method,
            "status": e.status,
            # The same step on the same model again: a retry, or a loop worth a look.
            "repeat": prev is not None and (prev.feature, prev.model) == (e.feature, e.model),
        })
        prev = e
    first = rows[0][0]
    scores = session.execute(
        select(TraceScore).where(TraceScore.trace_id == trace_id).order_by(TraceScore.timestamp, TraceScore.id)
    ).scalars().all()
    features = Counter(e.feature for e, _, _ in rows if e.feature)
    return {
        "trace_id": trace_id,
        "name": first.feature if len({e.feature for e, _, _ in rows}) == 1 and first.feature else first.project,
        "project": first.project,
        "team": first.team,
        "environment": first.environment,
        "customer_id": first.customer_id,
        "user_id": first.user_id,
        "started_at": start.isoformat(),
        "duration_ms": _duration_ms([(_aware(e.timestamp), e.latency_ms) for e, _, _ in rows]),
        "calls": len(rows),
        "cost_usd": round(total, 6),
        "errors": sum(1 for e, _, _ in rows if e.status != "success"),
        "repeats": sum(1 for s in steps if s["repeat"]),
        "top_step": features.most_common(1)[0][0] if features else None,
        "link": trace_link(trace_id, [e.attributes for e, _, _ in rows]),
        "steps": steps,
        "scores": [{"name": s.name, "data_type": s.data_type, "value": s.value, "label": s.label,
                    "source": s.source, "timestamp": _iso(s.timestamp)} for s in scores],
    }


# ---- scores in -------------------------------------------------------------------------------------------
def ingest_scores(session: Session, scores: list[ScoreIn], source: str = "api",
                  id_prefix: str = "api:") -> IngestResult:
    """Store trace scores. Idempotent on their id; scores without one are always added."""
    if not scores:
        return IngestResult(accepted=0, duplicates=0, rejected=0)
    now = datetime.now(UTC)
    rows = []
    for s in scores:
        if isinstance(s.value, bool):
            data_type, value = "BOOLEAN", 1.0 if s.value else 0.0
        elif s.value is not None:
            data_type, value = "NUMERIC", float(s.value)
        else:
            data_type, value = "CATEGORICAL", None
        rows.append({"external_id": f"{id_prefix}{s.id}"[:160] if s.id else None, "trace_id": s.trace_id,
                     "name": s.name, "data_type": data_type, "value": value, "label": s.label,
                     "source": source, "timestamp": (s.timestamp or now).astimezone(UTC)})
    stmt = insert_ignoring_conflicts(session, TraceScore, ["external_id"]).returning(TraceScore.id)
    inserted = len(session.execute(stmt, rows).all())
    session.commit()
    return IngestResult(accepted=inserted, duplicates=len(rows) - inserted, rejected=0)
