/**
 * Burnrate TypeScript SDK — zero-dependency usage reporting for LLM calls.
 *
 *   const br = new Burnrate({ apiKey: process.env.BURNRATE_API_KEY, project: "support-agent" });
 *   const res = await openai.chat.completions.create({ ... });
 *   br.trackOpenAI(res, { feature: "reply-drafting", customer_id: "acme" });
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
