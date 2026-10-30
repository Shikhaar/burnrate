"""Burnrate Python SDK — zero-dependency usage reporting for LLM calls.

    from burnrate_sdk import Burnrate

    br = Burnrate(api_key="br_live_...", project="support-agent")
    resp = openai_client.chat.completions.create(model="gpt-4o", messages=...)
    br.track_openai(resp, feature="reply-drafting", customer_id="acme")

    with br.attribute(feature="triage", customer_id="acme"):
        msg = anthropic_client.messages.create(...)
        br.track_anthropic(msg)

Events are queued and flushed in batches by a background thread; tracking never raises into
your request path and never sends prompts or completions — only token counts and metadata.
"""

from burnrate_sdk.client import Burnrate
from burnrate_sdk.normalize import from_anthropic_usage, from_gemini_usage, from_openai_usage

__all__ = ["Burnrate", "from_openai_usage", "from_anthropic_usage", "from_gemini_usage"]
__version__ = "0.1.0"
