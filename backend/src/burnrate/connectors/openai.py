"""OpenAI organization Costs API.

GET /v1/organization/costs — `start_time` in unix seconds, daily buckets, cursor pagination via
`next_page`. `amount.value` is in dollars.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time

import httpx

URL = "https://api.openai.com/v1/organization/costs"


def fetch_openai_costs(http: httpx.Client, api_key: str, start: date) -> Iterator[tuple[date, str, float]]:
    params: dict = {
        "start_time": int(datetime.combine(start, time(), UTC).timestamp()),
        "bucket_width": "1d",
        "limit": 31,
        "group_by": "line_item",
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    while True:
        resp = http.get(URL, params=params, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        for bucket in body.get("data", []):
            day = datetime.fromtimestamp(bucket["start_time"], UTC).date()
            for r in bucket.get("results", []):
                yield day, r.get("line_item") or "total", float(r["amount"]["value"])
        if not body.get("has_more"):
            return
        params["page"] = body["next_page"]
