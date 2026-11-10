/**
 * Burnrate TypeScript SDK — zero-dependency usage reporting for LLM calls.
 *
 *   const br = new Burnrate({ apiKey: process.env.BURNRATE_API_KEY, project: "support-agent" });
 *   const res = await openai.chat.completions.create({ ... });
 *   br.trackOpenAI(res, { feature: "reply-drafting", customer_id: "acme" });
 *
 * Vercel AI SDK: `wrapLanguageModel({ model, middleware: burnrateMiddleware(br) })`.
 *
 * Only token counts and metadata are sent — never prompts or completions. Tracking never throws.
 */

export interface UsageFields {
  input_tokens?: number;
  cached_input_tokens?: number;
  cache_write_tokens?: number;
  output_tokens?: number;
  reasoning_tokens?: number;
}

export interface Attribution {
  project?: string;
  feature?: string;
  team?: string;
  environment?: string;
  customer_id?: string;
  user_id?: string;
  trace_id?: string;
  latency_ms?: number;
  status?: "success" | "error" | "timeout" | "cancelled";
  request_id?: string;
  attributes?: Record<string, unknown>;
}

export interface BurnrateOptions {
  apiKey?: string;
  endpoint?: string;
  project?: string;
  environment?: string;
  team?: string;
  batchSize?: number;
  flushIntervalMs?: number;
  maxQueue?: number;
  fetch?: typeof fetch;
}

type Event = UsageFields & Attribution & { provider: string; model: string; timestamp: string };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Loose = any;

const n = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

export function fromOpenAIUsage(u: Loose): UsageFields {
  const prompt = n(u?.prompt_tokens) || n(u?.input_tokens);
  const output = n(u?.completion_tokens) || n(u?.output_tokens);
  const cached = n((u?.prompt_tokens_details ?? u?.input_tokens_details)?.cached_tokens);
  const reasoning = n((u?.completion_tokens_details ?? u?.output_tokens_details)?.reasoning_tokens);
  return { input_tokens: Math.max(0, prompt - cached), cached_input_tokens: cached, output_tokens: output,
           reasoning_tokens: reasoning };
}

export function fromAnthropicUsage(u: Loose): UsageFields {
  return {
    input_tokens: n(u?.input_tokens),
    cached_input_tokens: n(u?.cache_read_input_tokens),
    cache_write_tokens: n(u?.cache_creation_input_tokens),
    output_tokens: n(u?.output_tokens),
  };
}

export function fromGeminiUsage(u: Loose): UsageFields {
  const prompt = n(u?.promptTokenCount ?? u?.prompt_token_count);
  const cached = n(u?.cachedContentTokenCount ?? u?.cached_content_token_count);
  const thoughts = n(u?.thoughtsTokenCount ?? u?.thoughts_token_count);
  const out = n(u?.candidatesTokenCount ?? u?.candidates_token_count);
  return { input_tokens: Math.max(0, prompt - cached), cached_input_tokens: cached, output_tokens: out + thoughts,
           reasoning_tokens: thoughts };
}

export class Burnrate {
  readonly enabled: boolean;
  private queue: Event[] = [];
  private timer?: ReturnType<typeof setInterval>;
  private readonly opts: Required<Omit<BurnrateOptions, "team">> & { team?: string };

  constructor(options: BurnrateOptions = {}) {
    const env = typeof process !== "undefined" ? process.env : {};
    this.opts = {
      apiKey: options.apiKey ?? env.BURNRATE_API_KEY ?? "",
      endpoint: (options.endpoint ?? env.BURNRATE_ENDPOINT ?? "http://localhost:8000").replace(/\/$/, ""),
      project: options.project ?? "default",
      environment: options.environment ?? env.BURNRATE_ENVIRONMENT ?? "production",
      team: options.team,
      batchSize: options.batchSize ?? 100,
      flushIntervalMs: options.flushIntervalMs ?? 2000,
      maxQueue: options.maxQueue ?? 10_000,
      fetch: options.fetch ?? globalThis.fetch,
    };
    this.enabled = Boolean(this.opts.apiKey);
    if (this.enabled) {
      this.timer = setInterval(() => void this.flush(), this.opts.flushIntervalMs);
      // Don't keep a Node process alive just for telemetry.
      (this.timer as { unref?: () => void }).unref?.();
    }
  }

  track(provider: string, model: string, fields: UsageFields & Attribution = {}): void {
    if (!this.enabled) return;
    if (this.queue.length >= this.opts.maxQueue) {
      console.warn("[burnrate] queue full; dropping event");
      return;
    }
    this.queue.push({
      project: this.opts.project,
      environment: this.opts.environment,
      ...(this.opts.team ? { team: this.opts.team } : {}),
      ...fields,
      provider,
      model,
      request_id: fields.request_id ?? crypto.randomUUID(),
      timestamp: new Date().toISOString(),
    });
    if (this.queue.length >= this.opts.batchSize) void this.flush();
  }

  trackOpenAI<T>(response: T, attribution: Attribution = {}): T {
    const r = response as Loose;
    this.track("openai", r?.model ?? "unknown", { request_id: r?.id, ...fromOpenAIUsage(r?.usage), ...attribution });
    return response;
  }

  trackAnthropic<T>(response: T, attribution: Attribution = {}): T {
    const r = response as Loose;
    this.track("anthropic", r?.model ?? "unknown",
               { request_id: r?.id, ...fromAnthropicUsage(r?.usage), ...attribution });
    return response;
  }

  trackGemini<T>(response: T, model: string, attribution: Attribution = {}): T {
    const r = response as Loose;
    this.track("google", model, { ...fromGeminiUsage(r?.usageMetadata ?? r?.usage_metadata), ...attribution });
    return response;
  }

  /** Time an async call; usage can be filled from the result via `usageOf`. */
  async timed<T>(provider: string, model: string, call: () => Promise<T>,
                 usageOf: (r: T) => UsageFields = () => ({}), attribution: Attribution = {}): Promise<T> {
    const start = Date.now();
    try {
      const result = await call();
      this.track(provider, model, { ...usageOf(result), ...attribution, latency_ms: Date.now() - start });
      return result;
    } catch (err) {
      this.track(provider, model, { ...attribution, latency_ms: Date.now() - start, status: "error" });
      throw err;
    }
  }

  async flush(): Promise<void> {
    while (this.queue.length) {
      const batch = this.queue.splice(0, this.opts.batchSize);
      await this.send(batch);
    }
  }

  async close(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    await this.flush();
  }

  private async send(batch: Event[], retries = 3): Promise<void> {
    for (let attempt = 0; attempt < retries; attempt++) {
      try {
        const res = await this.opts.fetch(`${this.opts.endpoint}/v1/events`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-api-key": this.opts.apiKey },
          body: JSON.stringify({ events: batch }),
        });
        if (res.ok) return;
        if (res.status >= 400 && res.status < 500 && res.status !== 429) break; // not retryable
      } catch {
        // network error: retry
      }
      await new Promise((r) => setTimeout(r, 250 * 2 ** attempt));
    }
    console.warn(`[burnrate] dropped ${batch.length} events`);
  }
}

// ---- Vercel AI SDK -------------------------------------------------------------------------------

/**
 * Normalize AI SDK usage. Handles both shapes:
 *  - AI SDK 6 (LanguageModelV3): `inputTokens: { total, noCache, cacheRead, cacheWrite }`,
 *    `outputTokens: { total, text, reasoning }`.
 *  - AI SDK 5 (LanguageModelV2): flat numbers, `inputTokens` + `cachedInputTokens` + `reasoningTokens`.
 *    In v5 the Anthropic provider reports `inputTokens` WITHOUT cache reads while others include them,
 *    and cache writes only appear in provider metadata, so `provider` and `providerMetadata` are used here.
 */
export function fromAISDKUsage(u: Loose, provider = "", providerMetadata: Loose = undefined): UsageFields {
  if (u?.inputTokens !== null && typeof u?.inputTokens === "object") {
    const i = u.inputTokens;
    const o = u.outputTokens ?? {};
    const cacheRead = n(i.cacheRead);
    const cacheWrite = n(i.cacheWrite);
    const noCache = typeof i.noCache === "number" ? i.noCache : Math.max(0, n(i.total) - cacheRead - cacheWrite);
    return { input_tokens: noCache, cached_input_tokens: cacheRead, cache_write_tokens: cacheWrite,
             output_tokens: n(o.total), reasoning_tokens: n(o.reasoning) };
  }
  const cached = n(u?.cachedInputTokens);
  const cacheWrite = n(providerMetadata?.anthropic?.cacheCreationInputTokens);
  const input = provider === "anthropic" ? n(u?.inputTokens) : Math.max(0, n(u?.inputTokens) - cached);
  return { input_tokens: input, cached_input_tokens: cached, cache_write_tokens: cacheWrite,
           output_tokens: n(u?.outputTokens), reasoning_tokens: n(u?.reasoningTokens) };
}

/** "openai.chat" / "anthropic.messages" / "google.generative-ai" / "google.vertex.chat" -> registry name. */
export function aiSDKProvider(modelProvider: string | undefined): string {
  const base = (modelProvider ?? "unknown").split(".")[0].toLowerCase();
  if (base === "azure") return "openai";
  if (base === "vertex") return "google";
  if (base === "mistral" || base === "mistralai") return "mistral";
  return base;
}

export type AttributionFor = Attribution | ((params: Loose) => Attribution);

/**
 * Language model middleware for the Vercel AI SDK (v5 and v6). Records every `generateText`,
 * `streamText`, `generateObject` and `streamObject` call made through the wrapped model:
 *
 *   import { wrapLanguageModel } from "ai";
 *   const model = wrapLanguageModel({
 *     model: openai("gpt-4o"),
 *     middleware: burnrateMiddleware(br, { feature: "chat" }),
 *   });
 *
 * Per-call attribution: pass `providerOptions: { burnrate: { customer_id: "acme" } }` to the AI SDK
 * call, or give a function `(params) => Attribution`. Streams are recorded when the finish chunk
 * arrives; a stream that errors or is cancelled before finishing is recorded with that status.
 */
export function burnrateMiddleware(client: Burnrate, attribution: AttributionFor = {}) {
  const attrsFor = (params: Loose): Attribution => {
    const base = typeof attribution === "function" ? attribution(params) : attribution;
    const perCall = params?.providerOptions?.burnrate;
    return { ...base, ...(perCall && typeof perCall === "object" ? perCall : {}) };
  };
  const record = (model: Loose, params: Loose, start: number, fields: UsageFields & Attribution, modelId?: string) => {
    try {
      const provider = aiSDKProvider(model?.provider);
      client.track(provider, modelId || model?.modelId || "unknown",
                   { ...attrsFor(params), ...fields, latency_ms: Date.now() - start });
    } catch {
      // never throw into the caller
    }
  };

  return {
    // AI SDK 6 checks this; AI SDK 5 ignores it.
    specificationVersion: "v3" as const,

    async wrapGenerate({ doGenerate, params, model }: { doGenerate: () => Promise<Loose>; params: Loose;
                                                       model: Loose }) {
      const start = Date.now();
      try {
        const result = await doGenerate();
        record(model, params, start, {
          ...fromAISDKUsage(result?.usage, aiSDKProvider(model?.provider), result?.providerMetadata),
          ...(result?.response?.id ? { request_id: result.response.id } : {}),
        }, result?.response?.modelId);
        return result;
      } catch (err) {
        record(model, params, start, { status: isAbort(err) ? "cancelled" : "error" });
        throw err;
      }
    },

    async wrapStream({ doStream, params, model }: { doStream: () => Promise<Loose>; params: Loose; model: Loose }) {
      const start = Date.now();
      let result: Loose;
      try {
        result = await doStream();
      } catch (err) {
        record(model, params, start, { status: isAbort(err) ? "cancelled" : "error" });
        throw err;
      }
      let done = false;
      let responseId: string | undefined;
      let responseModel: string | undefined;
      const finish = (fields: UsageFields & Attribution) => {
        if (done) return;
        done = true;
        record(model, params, start, { ...fields, ...(responseId ? { request_id: responseId } : {}) }, responseModel);
      };
      const stream = (result.stream as ReadableStream<Loose>).pipeThrough(new TransformStream<Loose, Loose>({
        transform(chunk, controller) {
          if (chunk?.type === "response-metadata") {
            responseId = chunk.id ?? responseId;
            responseModel = chunk.modelId ?? responseModel;
          } else if (chunk?.type === "finish") {
            finish(fromAISDKUsage(chunk.usage, aiSDKProvider(model?.provider), chunk.providerMetadata));
          } else if (chunk?.type === "error") {
            finish({ status: "error" });
          }
          controller.enqueue(chunk);
        },
        flush() {
          finish({ status: "cancelled" }); // ended without a finish chunk
        },
        cancel() {
          finish({ status: "cancelled" });
        },
      } as Transformer<Loose, Loose>));
      return { ...result, stream };
    },
  };
}

function isAbort(err: unknown): boolean {
  return (err as { name?: string })?.name === "AbortError";
}
