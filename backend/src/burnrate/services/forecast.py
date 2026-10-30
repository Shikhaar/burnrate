"""Month-end spend forecast.

Model: least-squares linear trend over the last 28 days of daily spend, multiplied by a
day-of-week seasonality factor (weekday mean / overall mean). The band is ±1.645σ of residuals,
scaled by sqrt(remaining days) — a 90% interval assuming independent daily errors.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from sqlalchemy.orm import Session

from burnrate.services.analytics import Window, daily_totals, days_in_month, today

LOOKBACK_DAYS = 28


def _linear_fit(ys: list[float]) -> tuple[float, float]:
    n = len(ys)
    xs = range(n)
    mx, my = (n - 1) / 2, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx if sxx else 0.0
    return slope, my - slope * mx


def forecast_month(session: Session, on: date | None = None) -> dict:
    on = on or today()
    month_start = on.replace(day=1)
    month_end = month_start + timedelta(days=days_in_month(on) - 1)

    history_w = Window(on - timedelta(days=LOOKBACK_DAYS - 1), on)
    history = daily_totals(session, history_w)
    month = daily_totals(session, Window(month_start, on))
    mtd = sum(v[0] for v in month.values())

    ys = [history.get(d, (0.0, 0, 0))[0] for d in history_w.dates()]
    has_history = sum(1 for y in ys if y > 0) >= 7

    seasonal = dict.fromkeys(range(7), 1.0)
    slope = intercept = sigma = 0.0
    if has_history:
        overall = statistics.fmean(ys) or 1.0
        by_dow: dict[int, list[float]] = {}
        for d, y in zip(history_w.dates(), ys, strict=True):
            by_dow.setdefault(d.weekday(), []).append(y)
        seasonal = {k: (statistics.fmean(v) / overall if overall else 1.0) for k, v in by_dow.items()}
        deseason = [y / (seasonal[d.weekday()] or 1.0) for d, y in zip(history_w.dates(), ys, strict=True)]
        slope, intercept = _linear_fit(deseason)
        fitted = [
            (intercept + slope * i) * seasonal[d.weekday()] for i, d in enumerate(history_w.dates())
        ]
        sigma = statistics.pstdev([y - f for y, f in zip(ys, fitted, strict=True)])

    daily, future_sum, remaining = [], 0.0, 0
    d = month_start
    while d <= month_end:
        if d <= on:
            daily.append({"date": d.isoformat(), "actual_usd": round(month.get(d, (0.0, 0, 0))[0], 4),
                          "forecast_usd": None})
        else:
            remaining += 1
            if has_history:
                x = LOOKBACK_DAYS - 1 + (d - on).days
                pred = max(0.0, (intercept + slope * x) * seasonal.get(d.weekday(), 1.0))
            else:
                pred = mtd / on.day
            future_sum += pred
            daily.append({"date": d.isoformat(), "actual_usd": None, "forecast_usd": round(pred, 4)})
        d += timedelta(days=1)

    margin = 1.645 * sigma * math.sqrt(remaining) if remaining else 0.0
    projected = mtd + future_sum
    return {
        "month": month_start.strftime("%Y-%m"),
        "month_to_date_usd": round(mtd, 2),
        "projected_usd": round(projected, 2),
        "low_usd": round(max(mtd, projected - margin), 2),
        "high_usd": round(projected + margin, 2),
        "method": "linear_trend_x_weekday_seasonality" if has_history else "run_rate",
        "daily": daily,
    }
