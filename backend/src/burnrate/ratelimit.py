"""Token-bucket rate limiting.

Ingest (`/v1/*`) is limited per API key, so one noisy integration can't starve the others; the
dashboard API (`/api/*`) is limited per client IP. Both SDKs back off and retry on 429.

Buckets live in process memory: correct for a single API instance. Behind several replicas the
effective limit is per replica; swap `TokenBuckets` for a Redis-backed store to share it.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import OrderedDict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class TokenBuckets:
    """Thread-safe token buckets keyed by caller. Idle buckets are evicted beyond `max_keys`."""

    def __init__(self, per_minute: int, burst: int | None = None, max_keys: int = 10_000):
        self.rate = per_minute / 60.0
        self.capacity = float(burst or per_minute)
        self.max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def take(self, key: str, now: float | None = None) -> tuple[bool, int, float]:
        """Consume one token. Returns (allowed, tokens_remaining, seconds_until_next_token)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._buckets.pop(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            allowed = tokens >= 1
            if allowed:
                tokens -= 1
            self._buckets[key] = (tokens, now)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
        wait = 0.0 if tokens >= 1 else (1 - tokens) / self.rate if self.rate else math.inf
        return allowed, int(tokens), wait


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, ingest_per_minute: int, api_per_minute: int):
        super().__init__(app)
        self.ingest = TokenBuckets(ingest_per_minute) if ingest_per_minute > 0 else None
        self.api = TokenBuckets(api_per_minute) if api_per_minute > 0 else None

    def _bucket(self, request: Request) -> tuple[TokenBuckets | None, str]:
        path = request.url.path
        if path.startswith("/v1/") and self.ingest:
            # Key on a hash of the API key, so raw keys never sit in memory longer than the request.
            raw = request.headers.get("x-api-key", "")
            return self.ingest, "key:" + hashlib.sha256(raw.encode()).hexdigest()[:32]
        if path.startswith("/api/") and self.api:
            return self.api, "ip:" + (request.client.host if request.client else "unknown")
        return None, ""

    async def dispatch(self, request: Request, call_next) -> Response:
        buckets, key = self._bucket(request)
        if buckets is None:
            return await call_next(request)
        allowed, remaining, wait = buckets.take(key)
        limit = str(int(buckets.capacity))
        if not allowed:
            retry_after = str(max(1, math.ceil(wait)))
            return JSONResponse(
                {"detail": "Rate limit exceeded; retry later."},
                status_code=429,
                headers={"Retry-After": retry_after, "X-RateLimit-Limit": limit, "X-RateLimit-Remaining": "0"},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = limit
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
