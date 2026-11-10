"""LangChain callback handler: records every chat model / LLM call in a chain or agent.

    from burnrate_sdk import Burnrate
    from burnrate_sdk.langchain import BurnrateCallbackHandler

    br = Burnrate(project="support-agent")
    handler = BurnrateCallbackHandler(br, feature="triage")
    chain.invoke(inputs, config={"callbacks": [handler], "metadata": {"customer_id": "acme"}})

Provider and model come from LangChain's standard `ls_provider` / `ls_model_name` run metadata,
falling back to the response. Tokens come from `AIMessage.usage_metadata` (LangChain's normalized
usage), falling back to the provider's raw `token_usage`. Run metadata keys that match Burnrate
attribution fields (`feature`, `customer_id`, `user_id`, `team`, ...) are attached to the event.

`langchain-core` is not a dependency of this SDK: if it is installed the handler subclasses its
`BaseCallbackHandler`; otherwise it is a plain object exposing the same hooks.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from uuid import UUID

from burnrate_sdk.client import Burnrate
from burnrate_sdk.normalize import from_openai_usage

try:  # pragma: no cover - exercised only when langchain-core is installed
    from langchain_core.callbacks import BaseCallbackHandler as _Base
except ImportError:  # pragma: no cover
    _Base = object  # type: ignore[assignment,misc]

# LangChain `ls_provider` values -> Burnrate provider names (which key the price registry).
_PROVIDERS = {
    "google_genai": "google",
    "google_vertexai": "google",
    "google_anthropic_vertex": "anthropic",
    "azure": "openai",
    "azure_openai": "openai",
    "mistralai": "mistral",
}
_ATTRIBUTION_KEYS = ("project", "feature", "team", "environment", "customer_id", "user_id", "trace_id")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    v = obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    return default if v is None else v


def from_langchain_usage(usage: Any) -> dict:
    """LangChain `UsageMetadata`: `input_tokens` is the TOTAL prompt, including cache reads and writes."""
    total_in = int(_get(usage, "input_tokens", 0))
    in_details = _get(usage, "input_token_details", {})
    cache_read = int(_get(in_details, "cache_read", 0))
    cache_write = int(_get(in_details, "cache_creation", 0))
    return {
        "input_tokens": max(0, total_in - cache_read - cache_write),
        "cached_input_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "output_tokens": int(_get(usage, "output_tokens", 0)),
        "reasoning_tokens": int(_get(_get(usage, "output_token_details", {}), "reasoning", 0)),
    }


def _provider_from_serialized(serialized: dict | None) -> str | None:
    # e.g. {"id": ["langchain", "chat_models", "openai", "ChatOpenAI"]}
    path = [p.lower() for p in _get(serialized, "id", []) if isinstance(p, str)]
    for name in ("anthropic", "openai", "mistral", "google"):
        if any(name in p for p in path[:-1]):
            return name
    return None


class BurnrateCallbackHandler(_Base):  # type: ignore[misc,valid-type]
    """Report token usage for each LLM call LangChain makes. Never raises into the chain."""

    raise_error = False
    run_inline = True
    ignore_llm = False
    ignore_chat_model = False
    ignore_chain = True
    ignore_agent = True
    ignore_retriever = True
    ignore_retry = True
    ignore_custom_event = True

    def __init__(self, client: Burnrate, **attribution: Any):
        if _Base is not object:
            super().__init__()
        self.client = client
        self.attribution = attribution
        self._runs: dict[UUID, dict] = {}
        self._lock = threading.Lock()

    # ---- start -------------------------------------------------------------------------------
    def _start(self, serialized: dict | None, run_id: UUID, metadata: dict | None, tags: list | None,
               invocation_params: dict | None) -> None:
        metadata = metadata or {}
        params = invocation_params or {}
        provider = metadata.get("ls_provider") or _provider_from_serialized(serialized)
        model = metadata.get("ls_model_name") or params.get("model") or params.get("model_name")
        run = {
            "start": time.perf_counter(),
            "provider": _PROVIDERS.get(provider, provider) if provider else None,
            "model": model,
            "attrs": {k: metadata[k] for k in _ATTRIBUTION_KEYS if metadata.get(k) is not None},
        }
        with self._lock:
            self._runs[run_id] = run

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID, tags: list | None = None,
                     metadata: dict | None = None, invocation_params: dict | None = None, **kw: Any) -> None:
        self._start(serialized, run_id, metadata, tags, invocation_params)

    def on_chat_model_start(self, serialized: dict, messages: list, *, run_id: UUID, tags: list | None = None,
                            metadata: dict | None = None, invocation_params: dict | None = None,
                            **kw: Any) -> None:
        self._start(serialized, run_id, metadata, tags, invocation_params)

    # ---- end ---------------------------------------------------------------------------------
    def on_llm_end(self, response: Any, *, run_id: UUID, **kw: Any) -> None:
        try:
            run = self._pop(run_id)
            usage, model, request_id = self._extract(response)
            if run["start"] is None and not usage:
                return  # not a run we saw start, and nothing to report
            self._record(run, model, usage, "success", request_id)
        except Exception:  # noqa: BLE001 - telemetry must never break the chain
            pass

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kw: Any) -> None:
        try:
            run = self._pop(run_id)
            status = "timeout" if isinstance(error, TimeoutError) else "error"
            self._record(run, None, {}, status, None)
        except Exception:  # noqa: BLE001
            pass

    # ---- helpers -----------------------------------------------------------------------------
    def _pop(self, run_id: UUID) -> dict:
        with self._lock:
            return self._runs.pop(run_id, None) or {"start": None, "provider": None, "model": None, "attrs": {}}

    @staticmethod
    def _extract(response: Any) -> tuple[dict, str | None, str | None]:
        """Sum usage over all generations (n>1, batched prompts); fall back to `llm_output.token_usage`."""
        totals: dict[str, int] = {}
        model = request_id = None
        found = False
        for gens in _get(response, "generations", []):
            for gen in gens:
                msg = _get(gen, "message")
                meta = _get(msg, "response_metadata", {})
                model = model or meta.get("model_name") or meta.get("model")
                request_id = request_id or meta.get("id") or _get(msg, "id")
                usage = _get(msg, "usage_metadata")
                if usage:
                    found = True
                    for k, v in from_langchain_usage(usage).items():
                        totals[k] = totals.get(k, 0) + v
        llm_output = _get(response, "llm_output", {}) or {}
        model = model or llm_output.get("model_name") or llm_output.get("model")
        if not found and llm_output.get("token_usage"):
            totals = from_openai_usage(llm_output["token_usage"])
        return totals, model, request_id

    def _record(self, run: dict, model: str | None, usage: dict, status: str, request_id: str | None) -> None:
        latency = int((time.perf_counter() - run["start"]) * 1000) if run["start"] is not None else None
        fields = {**self.attribution, **run["attrs"], **usage}
        # The response names the exact model version that served the call; prefer it over the request.
        self.client.track(run["provider"] or "unknown", model or run["model"] or "unknown", latency_ms=latency,
                          status=status, request_id=request_id, **fields)
