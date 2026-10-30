import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from burnrate_sdk import Burnrate, from_anthropic_usage, from_gemini_usage, from_openai_usage


def test_openai_usage_excludes_cached_from_input():
    usage = {"prompt_tokens": 1000, "completion_tokens": 300,
             "prompt_tokens_details": {"cached_tokens": 400},
             "completion_tokens_details": {"reasoning_tokens": 120}}
    assert from_openai_usage(usage) == {"input_tokens": 600, "cached_input_tokens": 400,
                                        "output_tokens": 300, "reasoning_tokens": 120}


def test_openai_responses_api_shape():
    usage = SimpleNamespace(input_tokens=50, output_tokens=10, input_tokens_details=None,
                            output_tokens_details=SimpleNamespace(reasoning_tokens=4))
    assert from_openai_usage(usage)["reasoning_tokens"] == 4
    assert from_openai_usage(usage)["input_tokens"] == 50


def test_anthropic_and_gemini_shapes():
    a = from_anthropic_usage({"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 900,
                              "cache_creation_input_tokens": None})
    assert a == {"input_tokens": 10, "cached_input_tokens": 900, "cache_write_tokens": 0, "output_tokens": 5}
    g = from_gemini_usage({"prompt_token_count": 100, "cached_content_token_count": 40,
                           "candidates_token_count": 20, "thoughts_token_count": 30})
    assert g == {"input_tokens": 60, "cached_input_tokens": 40, "output_tokens": 50, "reasoning_tokens": 30}


@pytest.fixture
def server():
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            received.append({"key": self.headers["X-API-Key"],
                             "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
            self.send_response(202)
            self.end_headers()

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", received
    httpd.shutdown()


def test_events_are_batched_with_context_attribution(server):
    url, received = server
    br = Burnrate(api_key="br_test", endpoint=url, project="bot", flush_interval=60)
    with br.attribute(feature="faq", customer_id="acme"):
        br.track_anthropic({"id": "msg_1", "model": "claude-haiku-4-5",
                            "usage": {"input_tokens": 12, "output_tokens": 3}})
    br.track("openai", "gpt-4o", input_tokens=1)
    br.close()

    assert len(received) == 1 and received[0]["key"] == "br_test"
    first, second = received[0]["body"]["events"]
    assert first["feature"] == "faq" and first["customer_id"] == "acme" and first["request_id"] == "msg_1"
    assert first["project"] == "bot" and first["provider"] == "anthropic"
    assert "feature" not in second


def test_disabled_without_key_is_a_noop():
    br = Burnrate(api_key="", endpoint="http://127.0.0.1:9")
    br.track("openai", "gpt-4o")
    assert br._q.empty()


def test_timed_records_errors(server):
    url, received = server
    br = Burnrate(api_key="k", endpoint=url, flush_interval=60)
    with pytest.raises(ValueError), br.timed("openai", "gpt-4o") as usage:
        usage["input_tokens"] = 5
        raise ValueError("boom")
    br.close()
    ev = received[0]["body"]["events"][0]
    assert ev["status"] == "error" and ev["input_tokens"] == 5 and ev["latency_ms"] >= 0
