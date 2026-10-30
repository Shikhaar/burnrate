from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate.models import UsageEvent
from burnrate.pricing import PriceBook, compute_cost, to_event_cost
from burnrate.schemas import EventIn, IngestResult


def ingest_events(session: Session, events: list[EventIn], book: PriceBook | None = None) -> IngestResult:
    """Persist raw events and their derived cost. Idempotent on `request_id`."""
    book = book or PriceBook.from_session(session)

    incoming_ids = [e.request_id for e in events if e.request_id]
    seen: set[str] = set()
    if incoming_ids:
        seen = set(
            session.scalars(select(UsageEvent.request_id).where(UsageEvent.request_id.in_(incoming_ids)))
        )

    rows: list[UsageEvent] = []
    duplicates = 0
    now = datetime.now(UTC)
    for e in events:
        if e.request_id and e.request_id in seen:
            duplicates += 1
            continue
        if e.request_id:
            seen.add(e.request_id)
        ts = e.timestamp or now
        data = e.model_dump(exclude={"timestamp"})
        rows.append(UsageEvent(**data, timestamp=ts, day=ts.astimezone(UTC).date()))

    session.add_all(rows)
    session.flush()  # assign ids
    session.add_all(to_event_cost(r, compute_cost(r, book)) for r in rows)
    session.commit()
    return IngestResult(accepted=len(rows), duplicates=duplicates, rejected=0)
