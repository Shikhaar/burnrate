"""Token-bucket rate limiting.

Ingest (`/v1/*`) is limited per API key, so one noisy integration can't starve the others; the
dashboard API (`/api/*`) is limited per client IP. Both SDKs back off and retry on 429.

Two stores share one interface (`async acquire(key)`):
- `TokenBuckets`: process memory. Correct for a single API instance; with N replicas each one
  counts separately, so the effective limit is N x the setting.
- `RedisTokenBuckets`: one shared bucket per caller across all replicas (set BURNRATE_REDIS_URL).
  The refill-and-take step runs as a Lua script, so it's atomic, and uses Redis's clock, so
  replicas with skewed clocks agree. If Redis is unreachable requests are allowed through (and
  logged): a broken limiter must not take ingestion down with it.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections import OrderedDict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from burnrate.metrics import RATE_LIMITED

log = logging.getLogger("burnrate.ratelimit")


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

    async def acquire(self, key: str) -> tuple[bool, int, float]:
        return self.take(key)


# KEYS[1] = bucket key; ARGV = refill rate (tokens/s), capacity. Returns {allowed, tokens_left}.
# tokens_left is returned as a string because Redis truncates Lua numbers to integers.
_TAKE_SCRIPT = """
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) + 1)
return {allowed, tostring(tokens)}
"""


class RedisTokenBuckets:
    """Token buckets shared by every API replica, stored in Redis."""

    def __init__(self, client, per_minute: int, burst: int | None = None, prefix: str = "burnrate:rl:"):
        self.client = client
        self.rate = per_minute / 60.0
        self.capacity = float(burst or per_minute)
        self.prefix = prefix
        self._script = client.register_script(_TAKE_SCRIPT)

    async def acquire(self, key: str) -> tuple[bool, int, float]:
        try:
            allowed, left = await self._script(keys=[self.prefix + key], args=[self.rate, self.capacity])
        except Exception as e:  # noqa: BLE001 - fail open on any Redis problem
            log.warning("rate limiter store unavailable; allowing request", extra={"error": str(e)})
            return True, int(self.capacity), 0.0
        tokens = float(left)
        wait = 0.0 if tokens >= 1 else (1 - tokens) / self.rate
        return bool(int(allowed)), int(tokens), wait


Store = TokenBuckets | RedisTokenBuckets


def make_store(per_minute: int, redis_client=None) -> Store | None:
    if per_minute <= 0:
        return None
    return RedisTokenBuckets(redis_client, per_minute) if redis_client is not None else TokenBuckets(per_minute)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, ingest_per_minute: int, api_per_minute: int, redis_client=None):
        super().__init__(app)
        self.ingest = make_store(ingest_per_minute, redis_client)
        self.api = make_store(api_per_minute, redis_client)

    def _bucket(self, request: Request) -> tuple[Store | None, str]:
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
        allowed, remaining, wait = await buckets.acquire(key)
        limit = str(int(buckets.capacity))
        if not allowed:
            RATE_LIMITED.labels("ingest" if key.startswith("key:") else "api").inc()
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
