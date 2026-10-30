"""Stream simulated LLM calls through the real SDK so the dashboard updates live.

    burnrate create-key demo            # copy the key
    BURNRATE_API_KEY=br_live_... python examples/live_traffic.py --rps 5

Uses provider-shaped usage payloads (OpenAI / Anthropic / Gemini) so the SDK's normalization path
is exercised exactly as it would be with real responses. No provider API keys required.
"""

import argparse
import random
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from burnrate_sdk import Burnrate  # noqa: E402

CALLS = [
    ("anthropic", "claude-sonnet-4-5", "support-agent", "ticket-triage"),
    ("openai", "gpt-5", "support-agent", "reply-drafting"),
    ("openai", "gpt-4.1", "rag-search", "answer-synthesis"),
    ("google", "gemini-2.5-flash", "rag-search", "reranking"),
    ("anthropic", "claude-haiku-4-5", "data-extraction", "invoice-parsing"),
]
CUSTOMERS = ["acme-corp", "globex", "initech", "umbrella", "hooli"]


def fake_response(provider: str, model: str) -> dict:
    prompt, out = random.randint(800, 16_000), random.randint(80, 900)
    cached = int(prompt * random.uniform(0, 0.6))
    rid = f"live_{uuid.uuid4().hex[:16]}"
    if provider == "openai":
        return {"id": rid, "model": model, "usage": {
            "prompt_tokens": prompt, "completion_tokens": out,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": int(out * 0.3) if model.startswith("gpt-5") else 0}}}
    if provider == "anthropic":
        return {"id": rid, "model": model, "usage": {
            "input_tokens": prompt - cached, "output_tokens": out, "cache_read_input_tokens": cached}}
    return {"usage_metadata": {"prompt_token_count": prompt, "candidates_token_count": out,
                               "cached_content_token_count": cached}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rps", type=float, default=3.0)
    ap.add_argument("--seconds", type=float, default=0, help="0 = run until Ctrl-C")
    args = ap.parse_args()

    br = Burnrate(environment="production", flush_interval=1.0)
    if not br.enabled:
        sys.exit("Set BURNRATE_API_KEY (create one with `burnrate create-key demo`).")

    sent, start = 0, time.time()
    try:
        while not args.seconds or time.time() - start < args.seconds:
            provider, model, project, feature = random.choice(CALLS)
            resp = fake_response(provider, model)
            with br.attribute(project=project, feature=feature, customer_id=random.choice(CUSTOMERS)):
                latency = random.randint(400, 6000)
                if provider == "openai":
                    br.track_openai(resp, latency_ms=latency)
                elif provider == "anthropic":
                    br.track_anthropic(resp, latency_ms=latency)
                else:
                    br.track_gemini(resp, model=model, latency_ms=latency)
            sent += 1
            if sent % 25 == 0:
                print(f"sent {sent} calls")
            time.sleep(1 / args.rps)
    except KeyboardInterrupt:
        pass
    finally:
        br.close()
        print(f"done: {sent} calls")


if __name__ == "__main__":
    main()
