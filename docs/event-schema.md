# Usage event schema

Every LLM call becomes one immutable **usage event**. Cost is never stored on the event itself —
it is derived into `event_costs` using the versioned price registry, so a pricing fix is a
`burnrate pricing` re-run, not a data migration.

| Field | Type | Notes |
|---|---|---|
| `request_id` | string? | Idempotency key. Re-sent events with the same id are counted as `duplicates`, not double-billed. The SDK uses the provider's response id when available. |
| `timestamp` | ISO-8601 | Defaults to receive time. Naive timestamps are treated as UTC. |
| `provider` | string | `openai`, `anthropic`, `google`, `mistral`, `bedrock`, ... (lower-cased) |
| `model` | string | Dated snapshots resolve to their base model (`gpt-4o-2024-08-06` → `gpt-4o`). Other suffixes are different models (`o3-mini` ≠ `o3`); unknown models are recorded as `unpriced`, or use `provider_reported_cost_usd` if sent. |
| `operation` | string | `chat`, `embeddings`, ... |
| `project`, `feature`, `team`, `environment`, `customer_id`, `user_id`, `trace_id` | string? | Attribution dimensions. Calls sharing a `trace_id` form one trace (an agent run, a request chain) on the Traces page. |
| `input_tokens` | int | **Uncached** prompt tokens. |
| `cached_input_tokens` | int | Prompt tokens read from cache. |
| `cache_write_tokens` | int | Prompt tokens written to cache (Anthropic). |
| `output_tokens` | int | Completion tokens **including** reasoning tokens. |
| `reasoning_tokens` | int | Informational subset of `output_tokens`. |
| `latency_ms`, `status` | | `status` ∈ `success`, `error`, `timeout`, `cancelled`. Failed calls are still priced: providers bill them. |
| `provider_reported_cost_usd` | float? | Used only when the model has no registry price. |
| `attributes` | object | Free-form metadata. Never put prompts here. |

## Provider normalization

| Provider | Raw usage | Burnrate |
|---|---|---|
| OpenAI (Chat Completions) | `prompt_tokens` includes cached; `prompt_tokens_details.cached_tokens`; `completion_tokens_details.reasoning_tokens` | `input = prompt − cached`, `cached_input = cached`, `output = completion_tokens` |
| OpenAI (Responses) | `input_tokens`, `input_tokens_details.cached_tokens`, `output_tokens_details.reasoning_tokens` | same as above |
| Anthropic | `input_tokens` already excludes cache; `cache_read_input_tokens`; `cache_creation_input_tokens` | 1:1 |
| Gemini | `prompt_token_count` includes cached; `cached_content_token_count`; `thoughts_token_count` billed as output | `input = prompt − cached`, `output = candidates + thoughts` |
| OpenTelemetry GenAI | `gen_ai.usage.input_tokens` (full prompt), `gen_ai.usage.cache_read_input_tokens`, `gen_ai.usage.cache_creation_input_tokens` | `input = total − reads − writes` |

## LiteLLM

`burnrate sync litellm` reads the proxy's `GET /spend/logs/v2`, one UTC day at a time.

| Burnrate | LiteLLM spend log |
|---|---|
| `request_id` | `litellm:` + `request_id` |
| `provider`, `model` | `custom_llm_provider` + `model` (the deployment that served the call). `openai`, `anthropic`, `gemini`, `mistral` and Gemini on `vertex_ai` map to Burnrate's priced providers; others (`azure`, `bedrock`, …) keep their name and LiteLLM's cost |
| tokens | `prompt_tokens` includes cache reads and writes, which are in `metadata.additional_usage_values`: `input = prompt − cache_read_input_tokens − cache_creation_input_tokens`; `output = completion_tokens`; reasoning from `metadata.usage_object` |
| `project` | `spend_logs_metadata.project`, else the virtual key's alias, else `litellm` |
| `feature` | `spend_logs_metadata.feature`, else `model_group` (the public model name clients asked for) |
| `team` | `spend_logs_metadata.team`, else the key's team alias, else `team_id` |
| `customer_id` | `spend_logs_metadata.customer_id`, else `end_user` |
| `user_id`, `environment` | `user`; `spend_logs_metadata.environment`, else `production` |
| `trace_id` | `session_id`, so a session's calls form one trace |
| `provider_reported_cost_usd` | `spend` (used only when Burnrate has no price for the model) |
| cache hits | `cache_hit = "True"` means LiteLLM answered from its own cache and the provider billed nothing: recorded with 0 tokens and $0, the counts kept in `attributes` |

## Langfuse

`burnrate sync langfuse` reads `GET /api/public/v2/observations` for `GENERATION` and `EMBEDDING`
observations (Langfuse Cloud, or self-hosted v4+). It requests usage, model, metadata and trace
fields only, never inputs or outputs.

| Burnrate | Langfuse observation |
|---|---|
| `request_id` | `langfuse:` + `id` |
| `provider`, `model` | Langfuse doesn't record the provider, so it's worked out from the model name (`gpt-*`, `o3`, `claude-*`, `gemini-*`, `mistral-*`, …) or a `provider/` prefix; `metadata.provider` overrides. Unknown providers keep Langfuse's `totalCost` |
| tokens | `usageDetails` buckets don't overlap: `input` already excludes cache. Cache reads: `input_cached_tokens` / `cache_read_input_tokens`; writes: `cache_creation_input_tokens`; other `input_*` / `output_*` buckets (audio, reasoning) are added to input / output |
| `project` | `metadata.project`, else the Langfuse project's name |
| `feature` | `metadata.feature`, else the trace name, else the observation name |
| `team`, `customer_id` | `metadata.team`; `metadata.customer_id` |
| `user_id`, `trace_id` | `userId`; `traceId` |
| `environment` | `metadata.environment`, else Langfuse's environment (`default` becomes `production`) |
| `status`, `latency_ms` | `level = ERROR` is an error; `latency` × 1000 |
| `provider_reported_cost_usd` | `totalCost` (used only when Burnrate has no price for the model) |

## Cost formula

```
cost = input_tokens        × input_rate
     + cached_input_tokens × (cached_input_rate ?? input_rate)
     + cache_write_tokens  × (cache_write_rate ?? input_rate)
     + output_tokens       × output_rate            (rates are USD / 1M tokens)
```

The price used is the latest registry entry for that model whose `effective_from` ≤ the event's day (events older than the first entry use the earliest one).
