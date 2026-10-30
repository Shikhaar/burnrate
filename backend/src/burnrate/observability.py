"""Structured logging and per-request context.

Every request gets an id (taken from an incoming `X-Request-ID` or generated), echoed back in the
response header and attached to every log line written while handling it. In production
(`BURNRATE_LOG_FORMAT=json`) each line is one JSON object, ready for Loki / CloudWatch / Datadog.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
log = logging.getLogger("burnrate.http")

# Attributes every LogRecord has; anything else was passed via `extra=` and belongs in the JSON.
# (`color_message` is uvicorn's ANSI-coloured duplicate of msg.)
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "color_message"}
REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdFilter(logging.Filter):
    """Stamp the current request id onto the record when it's created, not when it's formatted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "request_id", None) is None:
            record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update({k: v for k, v in vars(record).items() if k not in _RESERVED and v is not None})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(f"{k}={v}" for k, v in vars(record).items() if k not in _RESERVED and v is not None)
        line = f"{record.levelname:<7} {record.name}: {record.getMessage()}"
        return f"{line} {extras}".rstrip()


def configure_logging(fmt: str = "text", level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Our middleware logs every request with more context; route uvicorn through the same handler.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        rid = incoming if 0 < len(incoming) <= 128 else uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"method": request.method, "path": request.url.path})
            raise
        finally:
            request_id_var.reset(token)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers[REQUEST_ID_HEADER] = rid
        if request.url.path != "/healthz":  # health probes would drown everything else
            level = logging.WARNING if response.status_code >= 500 else logging.INFO
            token = request_id_var.set(rid)
            try:
                log.log(level, "request", extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "client": request.client.host if request.client else None,
                })
            finally:
                request_id_var.reset(token)
        return response
