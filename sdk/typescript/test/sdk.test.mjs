import assert from "node:assert/strict";
import { test } from "node:test";

import { Burnrate, fromAnthropicUsage, fromGeminiUsage, fromOpenAIUsage } from "../dist/index.js";

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
