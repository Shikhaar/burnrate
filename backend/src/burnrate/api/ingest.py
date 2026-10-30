from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from burnrate.config import get_settings
from burnrate.db import get_session
from burnrate.otel import spans_to_events
from burnrate.schemas import EventBatchIn, IngestResult
from burnrate.security import require_ingest_key
from burnrate.services.ingest import ingest_events

router = APIRouter(prefix="/v1", tags=["ingest"], dependencies=[Depends(require_ingest_key)])


@router.post("/events", response_model=IngestResult, status_code=status.HTTP_202_ACCEPTED)
def post_events(batch: EventBatchIn, session: Session = Depends(get_session)) -> IngestResult:
    if len(batch.events) > get_settings().max_batch_size:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Batch exceeds {get_settings().max_batch_size} events; split it.",
        )
    return ingest_events(session, batch.events)


@router.post("/otlp/v1/traces", response_model=IngestResult, status_code=status.HTTP_202_ACCEPTED)
def post_otlp_traces(payload: dict, session: Session = Depends(get_session)) -> IngestResult:
    """OTLP/HTTP JSON receiver. Spans carrying OpenTelemetry GenAI attributes become usage events."""
    events, errors = spans_to_events(payload)
    result = ingest_events(session, events) if events else IngestResult(accepted=0, duplicates=0, rejected=0)
    result.rejected = len(errors)
    result.errors = errors
    return result
