import assert from "node:assert/strict";
import { test } from "node:test";

import {
  aiSDKProvider, Burnrate, burnrateMiddleware, fromAISDKUsage, fromAnthropicUsage, fromGeminiUsage, fromOpenAIUsage,
} from "../dist/index.js";

test("openai usage subtracts cached tokens from input", () => {
  assert.deepEqual(
    fromOpenAIUsage({ prompt_tokens: 1000, completion_tokens: 300, prompt_tokens_details: { cached_tokens: 400 },
                      completion_tokens_details: { reasoning_tokens: 120 } }),
    { input_tokens: 600, cached_input_tokens: 400, output_tokens: 300, reasoning_tokens: 120 },
  );
});

test("anthropic and gemini usage shapes", () => {
  assert.deepEqual(fromAnthropicUsage({ input_tokens: 10, output_tokens: 5, cache_read_input_tokens: 900 }),
                   { input_tokens: 10, cached_input_tokens: 900, cache_write_tokens: 0, output_tokens: 5 });
  assert.deepEqual(
    fromGeminiUsage({ promptTokenCount: 100, cachedContentTokenCount: 40, candidatesTokenCount: 20,
                      thoughtsTokenCount: 30 }),
    { input_tokens: 60, cached_input_tokens: 40, output_tokens: 50, reasoning_tokens: 30 },
  );
});

test("batches events with defaults and attribution", async () => {
  const calls = [];
  const fakeFetch = async (url, init) => {
    calls.push({ url, headers: init.headers, body: JSON.parse(init.body) });
    return new Response(null, { status: 202 });
  };
  const br = new Burnrate({ apiKey: "k", endpoint: "http://x/", project: "bot", fetch: fakeFetch,
                            flushIntervalMs: 60_000 });
  br.trackAnthropic({ id: "msg_1", model: "claude-haiku-4-5", usage: { input_tokens: 3, output_tokens: 1 } },
                    { feature: "faq", customer_id: "acme" });
  br.track("openai", "gpt-4o", { input_tokens: 1 });
  await br.close();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://x/v1/events");
  assert.equal(calls[0].headers["x-api-key"], "k");
  const [a, b] = calls[0].body.events;
  assert.equal(a.request_id, "msg_1");
  assert.equal(a.feature, "faq");
  assert.equal(a.project, "bot");
  assert.equal(b.feature, undefined);
});

test("timed records failures and rethrows", async () => {
  const sent = [];
  const br = new Burnrate({ apiKey: "k", fetch: async (_u, i) => { sent.push(JSON.parse(i.body)); return new Response(null, { status: 202 }); }, flushIntervalMs: 60_000 });
  await assert.rejects(br.timed("openai", "gpt-4o", async () => { throw new Error("boom"); }), /boom/);
  await br.close();
  assert.equal(sent[0].events[0].status, "error");
});

test("disabled without api key", async () => {
  const br = new Burnrate({ apiKey: "", fetch: async () => assert.fail("should not send") });
  br.track("openai", "gpt-4o");
  await br.close();
});

// ---- Vercel AI SDK middleware (driven directly; no `ai` dependency needed) -------------------------
const collector = () => {
  const sent = [];
  const br = new Burnrate({ apiKey: "k", flushIntervalMs: 60_000,
                            fetch: async (_u, i) => { sent.push(...JSON.parse(i.body).events); return new Response(null, { status: 202 }); } });
  return { br, sent };
};
const streamOf = (chunks) => new ReadableStream({ start(c) { chunks.forEach((x) => c.enqueue(x)); c.close(); } });
const drain = async (stream) => { for await (const _ of stream) { /* consume */ } };

test("ai sdk usage: v6 structured and v5 flat shapes", () => {
  assert.deepEqual(
    fromAISDKUsage({ inputTokens: { total: 130, noCache: 20, cacheRead: 100, cacheWrite: 10 },
                     outputTokens: { total: 9, text: 7, reasoning: 2 } }),
    { input_tokens: 20, cached_input_tokens: 100, cache_write_tokens: 10, output_tokens: 9, reasoning_tokens: 2 },
  );
  // v5: OpenAI's inputTokens include cache reads; Anthropic's do not, and cache writes live in metadata.
  assert.equal(fromAISDKUsage({ inputTokens: 120, cachedInputTokens: 100 }, "openai").input_tokens, 20);
  assert.deepEqual(
    fromAISDKUsage({ inputTokens: 20, outputTokens: 5, cachedInputTokens: 100 }, "anthropic",
                   { anthropic: { cacheCreationInputTokens: 40 } }),
    { input_tokens: 20, cached_input_tokens: 100, cache_write_tokens: 40, output_tokens: 5, reasoning_tokens: 0 },
  );
  assert.equal(aiSDKProvider("google.generative-ai"), "google");
  assert.equal(aiSDKProvider("azure.chat"), "openai");
});

test("middleware records generate calls with per-call attribution", async () => {
  const { br, sent } = collector();
  const mw = burnrateMiddleware(br, { feature: "chat" });
  const model = { provider: "openai.chat", modelId: "gpt-4o" };
  const result = { usage: { inputTokens: 50, outputTokens: 5 }, response: { id: "chatcmpl-9", modelId: "gpt-4o-2024-08-06" } };
  assert.equal(await mw.wrapGenerate({ doGenerate: async () => result, model,
                                       params: { providerOptions: { burnrate: { customer_id: "acme" } } } }), result);
  await assert.rejects(mw.wrapGenerate({ doGenerate: async () => { throw new Error("429"); }, model, params: {} }));
  await br.close();
  assert.equal(sent[0].model, "gpt-4o-2024-08-06");
  assert.equal(sent[0].request_id, "chatcmpl-9");
  assert.equal(sent[0].customer_id, "acme");
  assert.equal(sent[0].feature, "chat");
  assert.equal(sent[0].input_tokens, 50);
  assert.equal(sent[1].status, "error");
  assert.equal(sent[1].model, "gpt-4o");
});

test("middleware records streams once, on finish or when they end early", async () => {
  const { br, sent } = collector();
  const mw = burnrateMiddleware(br, (params) => ({ feature: params.feature }));
  const model = { provider: "anthropic.messages", modelId: "claude-haiku-4-5" };
  const finished = await mw.wrapStream({ model, params: { feature: "stream" }, doStream: async () => ({
    stream: streamOf([{ type: "response-metadata", id: "msg_7" }, { type: "text-delta", delta: "hi" },
                      { type: "finish", usage: { inputTokens: 3, outputTokens: 2 } }]) }) });
  await drain(finished.stream);
  const cut = await mw.wrapStream({ model, params: {}, doStream: async () => ({
    stream: streamOf([{ type: "text-delta", delta: "partial" }]) }) });
  await drain(cut.stream);
  await br.close();
  assert.equal(sent.length, 2);
  assert.equal(sent[0].request_id, "msg_7");
  assert.equal(sent[0].feature, "stream");
  assert.equal(sent[0].output_tokens, 2);
  assert.equal(sent[0].status, undefined); // success is the server default
  assert.equal(sent[1].status, "cancelled");
});
