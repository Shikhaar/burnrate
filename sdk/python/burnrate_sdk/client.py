from __future__ import annotations

import atexit
import contextvars
import json
import logging
import os
import queue
import threading
import time
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from burnrate_sdk.normalize import from_anthropic_usage, from_gemini_usage, from_openai_usage

log = logging.getLogger("burnrate")
_context: contextvars.ContextVar[dict] = contextvars.ContextVar("burnrate_attrs", default={})


class Burnrate:
    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        project: str = "default",
        environment: str | None = None,
        team: str | None = None,
        batch_size: int = 100,
        flush_interval: float = 2.0,
        max_queue: int = 10_000,
        enabled: bool = True,
    ):
        self.api_key = api_key or os.environ.get("BURNRATE_API_KEY", "")
        self.endpoint = (endpoint or os.environ.get("BURNRATE_ENDPOINT", "http://localhost:8000")).rstrip("/")
        self.defaults = {
            "project": project,
            "environment": environment or os.environ.get("BURNRATE_ENVIRONMENT", "production"),
            "team": team,
        }
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.enabled = enabled and bool(self.api_key)
        self._q: queue.Queue[dict] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        if self.enabled:
            self._worker = threading.Thread(target=self._run, name="burnrate-flush", daemon=True)
            self._worker.start()
            atexit.register(self.close)

    # ---- attribution -------------------------------------------------------------------------
    @contextmanager
    def attribute(self, **attrs: Any):
        """Attach attribution (feature, customer_id, user_id, trace_id, ...) to calls in this block."""
        token = _context.set({**_context.get(), **attrs})
        try:
            yield
        finally:
            _context.reset(token)

    # ---- tracking ----------------------------------------------------------------------------
    def track(self, provider: str, model: str, *, latency_ms: int | None = None, status: str = "success",
              request_id: str | None = None, **fields: Any) -> None:
        if not self.enabled:
            return
        event = {
            **{k: v for k, v in self.defaults.items() if v is not None},
            **_context.get(),
            **fields,
            "provider": provider,
            "model": model,
            "status": status,
            "latency_ms": latency_ms,
            "request_id": request_id or uuid.uuid4().hex,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._q.put_nowait(event)
        except queue.Full:
            log.warning("burnrate queue full; dropping event")

    def track_openai(self, response: Any, **fields: Any) -> Any:
        self.track("openai", _attr(response, "model", "unknown"), request_id=_attr(response, "id"),
                   **from_openai_usage(_attr(response, "usage")), **fields)
        return response

    def track_anthropic(self, response: Any, **fields: Any) -> Any:
        self.track("anthropic", _attr(response, "model", "unknown"), request_id=_attr(response, "id"),
                   **from_anthropic_usage(_attr(response, "usage")), **fields)
        return response

    def track_gemini(self, response: Any, model: str, **fields: Any) -> Any:
        self.track("google", model, **from_gemini_usage(_attr(response, "usage_metadata")), **fields)
        return response

    @contextmanager
    def timed(self, provider: str, model: str, **fields: Any):
        """Time a call and record it (usage can be filled in via the yielded dict)."""
        usage: dict = {}
        start = time.perf_counter()
        status = "success"
        try:
            yield usage
        except Exception:
            status = "error"
            raise
        finally:
            self.track(provider, model, latency_ms=int((time.perf_counter() - start) * 1000), status=status,
                       **usage, **fields)

    # ---- delivery ----------------------------------------------------------------------------
    def flush(self) -> None:
        batch: list[dict] = []
        while True:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
            if len(batch) >= self.batch_size:
                self._send(batch)
                batch = []
        if batch:
            self._send(batch)

    def close(self) -> None:
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=5)
        self.flush()

    def _run(self) -> None:
        while not self._stop.wait(self.flush_interval):
            self.flush()

    def _send(self, batch: list[dict], retries: int = 3) -> None:
        body = json.dumps({"events": batch}).encode()
        req = urllib.request.Request(
            f"{self.endpoint}/v1/events", data=body, method="POST",
            headers={"Content-Type": "application/json", "X-API-Key": self.api_key},
        )
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - endpoint is configured
                    if resp.status < 300:
                        return
            except Exception as e:  # never raise into the host app
                if attempt == retries - 1:
                    log.warning("burnrate: dropped %d events after %d attempts: %s", len(batch), retries, e)
                    return
            time.sleep(0.5 * 2**attempt)


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
