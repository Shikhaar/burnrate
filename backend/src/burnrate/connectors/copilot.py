"""GitHub Copilot spend from GitHub's billing usage report (enhanced billing platform).

GET https://api.github.com/organizations/{org}/settings/billing/usage?year=&month= — needs a token
for an organization owner or billing manager. Items cover every GitHub product; only those whose
`product` is Copilot are kept. Each (day, SKU) becomes one usage event under provider
`github-copilot` costed at `netAmount` (after included-usage discounts), with the gross amount and
quantity kept in `attributes`.

Only finished UTC days are imported. Events are idempotent per (org, day, SKU), so a day is read
once it is complete and is not revised afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time

import httpx

from burnrate.schemas import EventIn

URL = "https://api.github.com/organizations/{org}/settings/billing/usage"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _months(start: date, end: date) -> Iterator[tuple[int, int]]:
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch_copilot_events(http: httpx.Client, token: str, org: str, start: date,
                         today: date | None = None) -> Iterator[EventIn]:
    today = today or datetime.now(UTC).date()
    totals: dict[tuple[date, str], dict] = {}
    for year, month in _months(start, today):
        resp = http.get(URL.format(org=org), params={"year": year, "month": month},
                        headers={**HEADERS, "Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        for item in resp.json().get("usageItems", []):
            if "copilot" not in str(item.get("product", "")).lower():
                continue
            day = date.fromisoformat(str(item["date"])[:10])
            if day < start or day >= today:
                continue
            sku = str(item.get("sku") or "copilot")
            t = totals.setdefault((day, sku), {"net": 0.0, "gross": 0.0, "quantity": 0.0,
                                               "unit": item.get("unitType")})
            t["net"] += float(item.get("netAmount") or 0)
            t["gross"] += float(item.get("grossAmount") or 0)
            t["quantity"] += float(item.get("quantity") or 0)

    for (day, sku), t in sorted(totals.items()):
        yield EventIn(
            request_id=f"copilot:{org.lower()}:{day.isoformat()}:{sku.lower()}"[:128],
            timestamp=datetime.combine(day, time(), UTC),
            provider="github-copilot",
            model=sku[:120],
            operation="subscription" if "premium" not in sku.lower() else "premium_requests",
            project="github-copilot",
            feature="copilot",
            environment="development",
            provider_reported_cost_usd=round(t["net"], 6),
            attributes={"org": org, "sku": sku, "quantity": t["quantity"], "unit_type": t["unit"],
                        "gross_amount_usd": round(t["gross"], 6)},
        )
