"""Built-in alert timer: evaluate budgets and anomalies every N minutes inside the API process.

Several API replicas may each run it; that's safe because alert evaluation is deduplicated in the
database (each condition fires once) and delivery skips alerts already sent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from anyio import to_thread

from burnrate import db
from burnrate.services import alerts, app_settings

log = logging.getLogger("burnrate.scheduler")


def run_once() -> int:
    with db.SessionLocal() as session:
        try:
            fired = alerts.evaluate(session)
        except Exception as e:  # keep the timer alive; surface the reason on the settings page
            session.rollback()
            log.exception("alert evaluation failed")
            app_settings.put(session, app_settings.LAST_RUN_ERROR_KEY, f"{type(e).__name__}: {e}"[:500])
            fired = []
        else:
            app_settings.put(session, app_settings.LAST_RUN_ERROR_KEY, None)
        app_settings.put(session, app_settings.LAST_RUN_KEY, datetime.now(UTC).isoformat())
        return len(fired)


async def _loop(interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)  # first run after one interval, so startup stays fast
        n = await to_thread.run_sync(run_once)
        if n:
            log.info("fired %d alert(s)", n)


@contextlib.asynccontextmanager
async def alert_timer(interval_minutes: int):
    task = asyncio.create_task(_loop(interval_minutes * 60)) if interval_minutes > 0 else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
