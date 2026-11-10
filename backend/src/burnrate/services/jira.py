"""Create Jira tickets for alerts (Jira Cloud REST API v3, or Data Center with a personal token).

Auth is HTTP Basic with an Atlassian account email and API token. Use a dedicated bot account that
may only create issues in the chosen project: the token acts as that person.

Settings live in `app_settings` (changed from Settings → Alerts). Each alert creates at most one
ticket: before calling Jira, a delivery attempt is claimed with a conditional UPDATE, so two API
replicas checking at the same moment can't both create one.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from burnrate.models import Alert
from burnrate.services import app_settings

KEYS = ("site", "email", "api_token", "project_key", "issue_type", "min_severity")
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
MAX_ATTEMPTS = 5
LABEL = "burnrate"


def _key(name: str) -> str:
    return f"jira.{name}"


@dataclass(frozen=True)
class JiraConfig:
    site: str
    email: str
    api_token: str
    project_key: str
    issue_type: str
    min_severity: str = "high"

    @property
    def auth(self) -> tuple[str, str]:
        return self.email, self.api_token

    def issue_url(self, key: str) -> str:
        return f"{self.site}/browse/{key}"


class JiraError(Exception):
    """A problem worth showing an admin as-is."""


def load(session: Session) -> JiraConfig | None:
    values = {k: app_settings.get(session, _key(k)) for k in KEYS}
    if not all(values[k] for k in ("site", "email", "api_token", "project_key", "issue_type")):
        return None
    return JiraConfig(**{k: v for k, v in values.items() if v is not None})


def save(session: Session, cfg: JiraConfig) -> None:
    for k in KEYS:
        app_settings.put(session, _key(k), getattr(cfg, k))


def clear(session: Session) -> None:
    for k in KEYS:
        app_settings.put(session, _key(k), None)


def normalize_site(site: str) -> str:
    site = site.strip()
    if "://" not in site:
        site = "https://" + site
    parts = urlsplit(site)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise JiraError("Enter your Jira address, e.g. acme.atlassian.net.")
    return f"https://{parts.netloc.lower()}"


def _explain(resp: httpx.Response, what: str) -> str:
    if resp.status_code == 401:
        return "Jira didn't accept that email and API token."
    if resp.status_code == 403:
        return f"That Jira account isn't allowed to {what}."
    if resp.status_code == 404:
        return f"Jira couldn't find it ({what}). Check the project key."
    try:
        body = resp.json()
        msgs = list(body.get("errorMessages") or []) + [f"{k}: {v}" for k, v in (body.get("errors") or {}).items()]
    except ValueError:
        msgs = []
    return f"Jira answered HTTP {resp.status_code}" + (f": {'; '.join(msgs)}" if msgs else ".")


def verify(cfg: JiraConfig, http: httpx.Client) -> dict:
    """Check the login, the project, and the issue type, without creating anything."""
    try:
        me = http.get(f"{cfg.site}/rest/api/3/myself", auth=cfg.auth)
        if not me.is_success:
            raise JiraError(_explain(me, "sign in"))
        proj = http.get(f"{cfg.site}/rest/api/3/project/{cfg.project_key}", auth=cfg.auth)
        if not proj.is_success:
            raise JiraError(_explain(proj, f"see project {cfg.project_key}"))
    except httpx.HTTPError as e:
        raise JiraError(f"Couldn't reach {cfg.site} ({type(e).__name__}).") from None
    project = proj.json()
    types = [t.get("name") for t in project.get("issueTypes", []) if not t.get("subtask")]
    if types and cfg.issue_type.lower() not in {t.lower() for t in types if t}:
        raise JiraError(f"Project {cfg.project_key} has no issue type “{cfg.issue_type}”. "
                        f"Try one of: {', '.join(types)}.")
    return {"account": me.json().get("displayName"), "project_name": project.get("name"), "issue_types": types}


def _doc(text: str) -> dict:
    """Atlassian Document Format: one paragraph per line."""
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in text.splitlines() if line.strip()
    ]}


def create_issue(cfg: JiraConfig, http: httpx.Client, summary: str, description: str,
                 labels: tuple[str, ...] = ()) -> str:
    body = {"fields": {
        "project": {"key": cfg.project_key},
        "issuetype": {"name": cfg.issue_type},
        "summary": summary[:250],
        "description": _doc(description),
        "labels": [LABEL, *labels],
    }}
    try:
        resp = http.post(f"{cfg.site}/rest/api/3/issue", json=body, auth=cfg.auth)
    except httpx.HTTPError as e:
        raise JiraError(f"Couldn't reach {cfg.site} ({type(e).__name__}).") from None
    if not resp.is_success:
        raise JiraError(_explain(resp, f"create issues in {cfg.project_key}"))
    return resp.json()["key"]


def wants(cfg: JiraConfig, alert: Alert) -> bool:
    return SEVERITY_RANK.get(alert.severity, 0) >= SEVERITY_RANK.get(cfg.min_severity, 2)


def deliver_pending(session: Session, alerts: list[Alert], http: httpx.Client | None = None) -> int:
    """Create a ticket for each alert that qualifies and has none yet. Returns tickets created."""
    cfg = load(session)
    if cfg is None:
        return 0
    todo = [a for a in alerts if a.jira_issue_key is None and a.jira_attempts < MAX_ATTEMPTS and wants(cfg, a)]
    if not todo:
        return 0
    owns = http is None
    http = http or httpx.Client(timeout=15)
    created = 0
    try:
        for alert in todo:
            # Claim this attempt; another replica that read the same row loses the race and skips it.
            claimed = session.execute(
                update(Alert)
                .where(Alert.id == alert.id, Alert.jira_issue_key.is_(None), Alert.jira_attempts == alert.jira_attempts)
                .values(jira_attempts=Alert.jira_attempts + 1)
            ).rowcount
            session.commit()
            if claimed != 1:
                continue
            session.refresh(alert)
            try:
                alert.jira_issue_key = create_issue(
                    cfg, http, f"[Burnrate] {alert.title}",
                    f"{alert.detail}\n\nSeverity: {alert.severity}. Raised by Burnrate "
                    f"{alert.created_at.strftime('%Y-%m-%d %H:%M UTC')}.",
                    labels=(f"burnrate-{alert.kind}",),
                )
                alert.jira_error = None
                created += 1
            except JiraError as e:
                alert.jira_error = str(e)[:500]
            session.commit()
    finally:
        if owns:
            http.close()
    return created


def status(session: Session) -> dict:
    cfg = load(session)
    last = session.scalars(select(Alert).where(Alert.jira_issue_key.is_not(None))
                           .order_by(Alert.created_at.desc()).limit(1)).first()
    failed = session.scalars(select(Alert).where(Alert.jira_issue_key.is_(None), Alert.jira_error.is_not(None))
                             .order_by(Alert.created_at.desc()).limit(1)).first()
    return {
        "connected": cfg is not None,
        "site": cfg.site if cfg else None,
        "email": cfg.email if cfg else None,
        "token_hint": f"…{cfg.api_token[-4:]}" if cfg else None,
        "project_key": cfg.project_key if cfg else None,
        "issue_type": cfg.issue_type if cfg else None,
        "min_severity": cfg.min_severity if cfg else "high",
        "last_ticket": ({"key": last.jira_issue_key, "url": cfg.issue_url(last.jira_issue_key) if cfg else None,
                         "title": last.title} if last else None),
        "last_failure": {"error": failed.jira_error, "title": failed.title} if failed else None,
    }
