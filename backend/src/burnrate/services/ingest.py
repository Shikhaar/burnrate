from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from burnrate.models import UsageEvent
from burnrate.pricing import PriceBook, compute_cost, to_event_cost
from burnrate.schemas import EventIn, IngestResult

# Columns the pricing engine needs, returned straight from the INSERT so we never re-read rows.
_PRICED_COLUMNS = (
    UsageEvent.id, UsageEvent.day, UsageEvent.provider, UsageEvent.model, UsageEvent.input_tokens,
    UsageEvent.cached_input_tokens, UsageEvent.cache_write_tokens, UsageEvent.output_tokens,
    UsageEvent.provider_reported_cost_usd,
)


def _insert_ignoring_duplicates(session: Session):
    """INSERT ... ON CONFLICT (request_id) DO NOTHING, so concurrent batches can't fail each other."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:  # pragma: no cover - other databases fall back to the pre-check only
        return insert(UsageEvent)
    return dialect_insert(UsageEvent).on_conflict_do_nothing(index_elements=["request_id"])


def ingest_events(session: Session, events: list[EventIn], book: PriceBook | None = None) -> IngestResult:
    """Persist raw events and their derived cost. Idempotent on `request_id`, including under concurrency."""
    book = book or PriceBook.from_session(session)

    incoming_ids = [e.request_id for e in events if e.request_id]
    seen: set[str] = set()
    if incoming_ids:
        seen = set(
            session.scalars(select(UsageEvent.request_id).where(UsageEvent.request_id.in_(incoming_ids)))
        )

    rows: list[dict] = []
    now = datetime.now(UTC)
    for e in events:
        if e.request_id:
            if e.request_id in seen:
                continue
            seen.add(e.request_id)
        ts = (e.timestamp or now).astimezone(UTC)
        rows.append({**e.model_dump(exclude={"timestamp"}), "timestamp": ts, "day": ts.date()})

    inserted = []
    if rows:
        stmt = _insert_ignoring_duplicates(session).returning(*_PRICED_COLUMNS)
        inserted = session.execute(stmt, rows).all()
    costs = [to_event_cost(r, compute_cost(r, book)) for r in (SimpleNamespace(**row._mapping) for row in inserted)]
    if costs:
        session.add_all(costs)
    session.commit()
    return IngestResult(accepted=len(inserted), duplicates=len(events) - len(inserted), rejected=0)

