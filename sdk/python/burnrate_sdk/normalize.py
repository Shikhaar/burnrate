"""Map each provider's usage object onto Burnrate's normalized token fields.

Burnrate convention: `input_tokens` excludes cache reads/writes; `output_tokens` includes reasoning.
Accepts SDK objects or plain dicts.
"""

from __future__ import annotations

from typing import Any


def _get(obj: Any, key: str, default: Any = 0) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        v = obj.get(key, default)
    else:
        v = getattr(obj, key, default)
    return default if v is None else v


def from_openai_usage(usage: Any) -> dict:
    """Chat Completions (`prompt_tokens`...) and Responses API (`input_tokens`...) shapes."""
    prompt = _get(usage, "prompt_tokens") or _get(usage, "input_tokens")
    output = _get(usage, "completion_tokens") or _get(usage, "output_tokens")
    in_details = _get(usage, "prompt_tokens_details", None) or _get(usage, "input_tokens_details", None)
    out_details = _get(usage, "completion_tokens_details", None) or _get(usage, "output_tokens_details", None)
    cached = _get(in_details, "cached_tokens")
    return {
        "input_tokens": max(0, prompt - cached),
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_tokens": _get(out_details, "reasoning_tokens"),
    }


def from_anthropic_usage(usage: Any) -> dict:
    """Anthropic already reports uncached input separately from cache reads/writes."""
    return {
        "input_tokens": _get(usage, "input_tokens"),
        "cached_input_tokens": _get(usage, "cache_read_input_tokens"),
        "cache_write_tokens": _get(usage, "cache_creation_input_tokens"),
        "output_tokens": _get(usage, "output_tokens"),
    }


def from_gemini_usage(usage: Any) -> dict:
    prompt = _get(usage, "prompt_token_count")
    cached = _get(usage, "cached_content_token_count")
    thoughts = _get(usage, "thoughts_token_count")
    return {
        "input_tokens": max(0, prompt - cached),
        "cached_input_tokens": cached,
        "output_tokens": _get(usage, "candidates_token_count") + thoughts,
        "reasoning_tokens": thoughts,
    }
