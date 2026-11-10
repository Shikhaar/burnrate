"""Settings changed from the dashboard. Admin only (see burnrate.auth)."""

from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from burnrate.auth import require_admin
from burnrate.config import get_settings
from burnrate.db import get_session
from burnrate.services import alerts, app_settings, jira

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_admin)])


class WebhookIn(BaseModel):
    webhook_url: str = Field(..., min_length=1, max_length=app_settings.MAX_URL_LENGTH)


def _alerts_status(session: Session) -> dict:
    url, source = app_settings.webhook_url(session)
    interval = get_settings().alert_interval_minutes
    return {
        "connected": url is not None,
        "source": source,  # "dashboard" | "environment" | null
        "webhook_hint": app_settings.mask_webhook(url) if url else None,
        "is_slack": app_settings.is_slack(url) if url else None,
        "timer": {"enabled": interval > 0, "interval_minutes": interval,
                  "last_run_at": app_settings.get(session, app_settings.LAST_RUN_KEY),
                  "last_run_error": app_settings.get(session, app_settings.LAST_RUN_ERROR_KEY)},
        **alerts.delivery_status(session),
    }


@router.get("/alerts")
def get_alert_settings(session: Session = Depends(get_session)) -> dict:
    return _alerts_status(session)


@router.put("/alerts")
def save_webhook(body: WebhookIn, session: Session = Depends(get_session)) -> dict:
    """Save the webhook only if a test message gets through, so a typo can't silently drop alerts."""
    try:
        url = app_settings.validate_webhook(body.webhook_url)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    problem = alerts.send_test(url)
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    app_settings.put(session, app_settings.WEBHOOK_KEY, url)
    return _alerts_status(session)


@router.delete("/alerts")
def disconnect_webhook(session: Session = Depends(get_session)) -> dict:
    """Removes the dashboard setting. A BURNRATE_ALERT_WEBHOOK_URL on the server still applies."""
    app_settings.put(session, app_settings.WEBHOOK_KEY, None)
    return _alerts_status(session)


@router.post("/alerts/test")
def test_webhook(session: Session = Depends(get_session)) -> dict:
    url, _ = app_settings.webhook_url(session)
    if not url:
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect a webhook first.")
    problem = alerts.send_test(url)
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    return {"sent": True}


# ---- Jira -------------------------------------------------------------------------------------------------
class JiraIn(BaseModel):
    site: str = Field(..., min_length=3, max_length=200)
    email: str = Field(..., min_length=3, max_length=254)
    # Optional when editing a saved connection: leave it out to keep the current token.
    api_token: str | None = Field(None, min_length=1, max_length=500)
    project_key: str = Field(..., min_length=1, max_length=20)
    issue_type: str = Field("Task", min_length=1, max_length=60)
    min_severity: Literal["high", "medium"] = "high"


@router.get("/jira")
def get_jira(session: Session = Depends(get_session)) -> dict:
    return jira.status(session)


@router.put("/jira")
def save_jira(body: JiraIn, session: Session = Depends(get_session)) -> dict:
    """Save only after Jira confirms the login, project and issue type (nothing is created)."""
    existing = jira.load(session)
    token = body.api_token or (existing.api_token if existing else None)
    if not token:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Paste a Jira API token.")
    try:
        cfg = jira.JiraConfig(site=jira.normalize_site(body.site), email=body.email.strip(), api_token=token.strip(),
                              project_key=body.project_key.strip().upper(), issue_type=body.issue_type.strip(),
                              min_severity=body.min_severity)
        with httpx.Client(timeout=15) as http:
            found = jira.verify(cfg, http)
    except jira.JiraError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    jira.save(session, cfg)
    return {**jira.status(session), "verified": found}


@router.delete("/jira")
def disconnect_jira(session: Session = Depends(get_session)) -> dict:
    jira.clear(session)
    return jira.status(session)


@router.post("/jira/test")
def jira_test_ticket(session: Session = Depends(get_session)) -> dict:
    """Create one real ticket so the admin can see where alerts will land."""
    cfg = jira.load(session)
    if cfg is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect Jira first.")
    try:
        with httpx.Client(timeout=15) as http:
            key = jira.create_issue(cfg, http, "[Burnrate] Test ticket: safe to delete",
                                    "Burnrate is connected. Alerts will be filed in this project.",
                                    labels=("burnrate-test",))
    except jira.JiraError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    return {"key": key, "url": cfg.issue_url(key)}
