"""Work out which provider served a model, for sources that don't say (Langfuse) or say it their own way
(LiteLLM's `custom_llm_provider`). Burnrate prices OpenAI, Anthropic, Google and Mistral models from its
registry; anything else keeps its own provider name and is costed at what the source reported.
"""

from __future__ import annotations

# Gateway / SDK names for providers Burnrate prices directly. Cloud resellers (azure, bedrock,
# vertex_ai for non-Gemini models) stay separate: their bills aren't the provider's bill.
ALIASES = {
    "openai": "openai", "text-completion-openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google", "google": "google", "google_ai_studio": "google",
    "mistral": "mistral", "mistralai": "mistral", "codestral": "mistral",
}

# Model-name prefixes, checked in order.
PREFIXES = (
    (("gpt-", "o1", "o3", "o4", "chatgpt-", "text-embedding-3", "text-embedding-ada", "dall-e", "whisper",
      "tts-"), "openai"),
    (("claude",), "anthropic"),
    (("gemini", "text-embedding-004", "gemma"), "google"),
    (("mistral", "ministral", "codestral", "pixtral", "magistral", "devstral", "open-mistral"), "mistral"),
)


def split_model(model: str) -> tuple[str | None, str]:
    """'anthropic/claude-sonnet-4-5' -> ('anthropic', 'claude-sonnet-4-5'); plain names have no prefix."""
    model = (model or "").strip()
    if "/" in model:
        head, tail = model.split("/", 1)
        return head.lower(), tail
    return None, model


def from_model_name(model: str) -> str | None:
    name = model.lower()
    for prefixes, provider in PREFIXES:
        if name.startswith(prefixes):
            return provider
    return None


def resolve(model: str, hint: str | None = None) -> tuple[str, str]:
    """(provider, model) for an event. `hint` is what the source says the provider is, if anything."""
    prefix, bare = split_model(model)
    hint = (hint or prefix or "").lower() or None
    by_name = from_model_name(bare)
    if hint in ALIASES:
        return ALIASES[hint], bare
    if hint in ("vertex_ai", "vertex_ai_beta") and by_name == "google":
        return "google", bare  # Gemini on Vertex is billed at Google's list prices
    if hint:
        return hint, bare  # azure, bedrock, groq, ...: not priced by Burnrate, uses the reported cost
    return by_name or "unknown", bare
