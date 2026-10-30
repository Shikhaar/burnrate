"""Anthropic Admin API cost report.

GET /v1/organizations/cost_report — daily buckets, cursor pagination via `next_page`.
`amount` is a decimal string in the currency's lowest unit (cents for USD), so divide by 100.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time

import httpx

URL = "https://api.anthropic.com/v1/organizations/cost_report"


def fetch_anthropic_costs(http: httpx.Client, api_key: str, start: date) -> Iterator[tuple[date, str, float]]:
    params: dict = {
        "starting_at": datetime.combine(start, time(), UTC).isoformat().replace("+00:00", "Z"),
        "bucket_width": "1d",
        "limit": 31,
        "group_by[]": "description",
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    while True:
        resp = http.get(URL, params=params, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        for bucket in body.get("data", []):
            day = datetime.fromisoformat(bucket["starting_at"].replace("Z", "+00:00")).date()
            for r in bucket.get("results", []):
                yield day, r.get("description") or "total", float(r["amount"]) / 100
        if not body.get("has_more"):
            return
        params["page"] = body["next_page"]
