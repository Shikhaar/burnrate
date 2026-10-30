"""Cost-optimization recommendations derived from the last 30 days of usage.

Each rule estimates monthly savings from observed traffic, so recommendations are ranked by money,
not by how clever the rule is.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from burnrate.models import EventCost, UsageEvent
from burnrate.pricing import MTOK, PriceBook
from burnrate.services.analytics import Window, _base, _in

WINDOW_DAYS = 30
MONTH_FACTOR = 30 / WINDOW_DAYS

# Same-provider cheaper tier to suggest for light workloads.
DOWNGRADES = {
    ("openai", "gpt-5"): "gpt-5-mini",
    ("openai", "gpt-4.1"): "gpt-4.1-mini",
    ("openai", "gpt-4o"): "gpt-4o-mini",
    ("openai", "o3"): "o4-mini",
    ("anthropic", "claude-opus-4-5"): "claude-haiku-4-5",
    ("anthropic", "claude-opus-4-1"): "claude-haiku-4-5",
    ("anthropic", "claude-sonnet-4-5"): "claude-haiku-4-5",
    ("anthropic", "claude-sonnet-4"): "claude-haiku-4-5",
    ("google", "gemini-2.5-pro"): "gemini-2.5-flash",
}
LIGHT_INPUT_TOKENS = 2_000
LIGHT_OUTPUT_TOKENS = 400
CACHE_MIN_AVG_INPUT = 4_000
CACHE_MAX_HIT_RATIO = 0.15
ASSUMED_CACHEABLE_SHARE = 0.5
MIN_SAVINGS_USD = 10.0


def _workloads(session: Session, w: Window):
    stmt = _in(
        _base(
            UsageEvent.provider,
            UsageEvent.model,
            UsageEvent.project,
            UsageEvent.feature,
            func.count(UsageEvent.id),
            func.sum(UsageEvent.input_tokens),
            func.sum(UsageEvent.cached_input_tokens),
            func.sum(UsageEvent.cache_write_tokens),
            func.sum(UsageEvent.output_tokens),
            func.sum(EventCost.total_cost_usd),
        ),
        w,
    ).where(UsageEvent.status == "success")
    return session.execute(
        stmt.group_by(UsageEvent.provider, UsageEvent.model, UsageEvent.project, UsageEvent.feature)
    ).all()


def generate(session: Session) -> list[dict]:
    w = Window.last(WINDOW_DAYS)
    book = PriceBook.from_session(session)
    out: list[dict] = []

    for provider, model, project, feature, n, inp, cached, written, outp, cost in _workloads(session, w):
        inp, cached, written, outp, cost = int(inp), int(cached), int(written), int(outp), float(cost)
        scope = f"{project}/{feature or '*'}"
        avg_in = (inp + cached + written) / n
        avg_out = outp / n

        # 1) Premium model on light requests -> cheaper tier.
        base_model = next((m for (p, m) in DOWNGRADES if p == provider and model.startswith(m)), None)
        if base_model and avg_in <= LIGHT_INPUT_TOKENS and avg_out <= LIGHT_OUTPUT_TOKENS:
            target = DOWNGRADES[(provider, base_model)]
            tp = book.resolve(provider, target, w.end)
            if tp:
                cached_rate = tp.cached_input_per_mtok or tp.input_per_mtok
                alt = (inp * tp.input_per_mtok + cached * cached_rate + written * tp.input_per_mtok
                       + outp * tp.output_per_mtok) / MTOK
                savings = (cost - alt) * MONTH_FACTOR
                if savings >= MIN_SAVINGS_USD:
                    out.append({
                        "id": f"downgrade:{provider}:{model}:{scope}",
                        "kind": "model_downgrade",
                        "title": f"Route {scope} from {model} to {target}",
                        "detail": f"{n:,} requests averaged {avg_in:,.0f} input / {avg_out:,.0f} output tokens. "
                                  f"Light, short-form traffic like this rarely needs a frontier model; "
                                  f"A/B test {target} on it.",
                        "estimated_monthly_savings_usd": round(savings, 2),
                        "scope": scope,
                    })

        # 2) Long, repeated prompts with little caching -> prompt caching.
        price = book.resolve(provider, model, w.end)
        hit_ratio = cached / (inp + cached) if inp + cached else 0.0
        if (price and price.cached_input_per_mtok is not None and avg_in >= CACHE_MIN_AVG_INPUT
                and hit_ratio <= CACHE_MAX_HIT_RATIO):
            cacheable = inp * ASSUMED_CACHEABLE_SHARE
            savings = cacheable * (price.input_per_mtok - price.cached_input_per_mtok) / MTOK * MONTH_FACTOR
            if savings >= MIN_SAVINGS_USD:
                out.append({
                    "id": f"cache:{provider}:{model}:{scope}",
                    "kind": "prompt_caching",
                    "title": f"Enable prompt caching for {scope}",
                    "detail": f"Prompts average {avg_in:,.0f} tokens but only {hit_ratio:.0%} are cache hits on "
                              f"{model}. Caching a stable ~{ASSUMED_CACHEABLE_SHARE:.0%} prefix (system prompt, "
                              f"tools, retrieved docs) cuts that input cost by ~90%.",
                    "estimated_monthly_savings_usd": round(savings, 2),
                    "scope": scope,
                })

    # 3) Money burned on failed calls.
    failed = session.execute(
        _in(_base(UsageEvent.project, UsageEvent.feature, func.count(UsageEvent.id),
                  func.sum(EventCost.total_cost_usd)), w)
        .where(UsageEvent.status != "success")
        .group_by(UsageEvent.project, UsageEvent.feature)
    ).all()
    for project, feature, n, cost in failed:
        savings = float(cost) * MONTH_FACTOR
        if savings >= MIN_SAVINGS_USD:
            scope = f"{project}/{feature or '*'}"
            out.append({
                "id": f"errors:{scope}",
                "kind": "failed_requests",
                "title": f"Stop paying for failed calls in {scope}",
                "detail": f"{n:,} requests ended in error/timeout but still consumed tokens. Add timeouts, "
                          f"cap retries with backoff, and validate inputs before calling the model.",
                "estimated_monthly_savings_usd": round(savings, 2),
                "scope": scope,
            })

    out.sort(key=lambda i: i["estimated_monthly_savings_usd"], reverse=True)
    return out
