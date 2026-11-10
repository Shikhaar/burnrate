from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate.auth import Principal, require_dashboard_access, require_unscoped
from burnrate.db import get_session
from burnrate.models import Budget, PriceEntry
from burnrate.schemas import BudgetIn, Dimension
from burnrate.services import alerts, analytics, anomalies, budgets, forecast, insights, reconciliation

router = APIRouter(prefix="/api", tags=["dashboard"], dependencies=[Depends(require_dashboard_access)])

Days = Annotated[int, Query(ge=1, le=365)]
SessionDep = Annotated[Session, Depends(get_session)]
WhoDep = Annotated[Principal, Depends(require_dashboard_access)]


@router.get("/meta")
def get_meta(session: SessionDep) -> dict:
    return analytics.meta(session)


@router.get("/overview")
def get_overview(session: SessionDep, days: Days = 30) -> dict:
    return analytics.overview(session, days)


@router.get("/breakdown")
def get_breakdown(
    session: SessionDep, dimension: Dimension = "model", days: Days = 30,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict:
    return analytics.breakdown(session, dimension, days, limit)


@router.get("/timeseries")
def get_timeseries(session: SessionDep, group_by: Dimension = "provider", days: Days = 30) -> dict:
    return analytics.timeseries(session, group_by, days)


@router.get("/events")
def get_events(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    provider: str | None = None,
    model: str | None = None,
    project: str | None = None,
    feature: str | None = None,
    customer: str | None = None,
) -> dict:
    filters = {"provider": provider, "model": model, "project": project, "feature": feature, "customer": customer}
    return analytics.list_events(session, limit, offset, filters)


@router.get("/anomalies")
def get_anomalies(session: SessionDep, days: Days = 30) -> dict:
    return {"items": anomalies.detect(session, days)}


@router.get("/forecast")
def get_forecast(session: SessionDep) -> dict:
    return forecast.forecast_month(session)


@router.get("/reconciliation", dependencies=[Depends(require_unscoped)])  # provider bills span every team
def get_reconciliation(session: SessionDep, days: Days = 30) -> dict:
    return reconciliation.reconcile(session, days)


@router.get("/insights")
def get_insights(session: SessionDep) -> dict:
    return {"items": insights.generate(session)}


def _visible(who: Principal, scope_dimension: str | None, scope_value: str | None) -> bool:
    """Team-limited users only see and manage budgets for one of their teams."""
    return not who.scoped or (scope_dimension == "team" and scope_value in who.teams)


@router.get("/budgets")
def get_budgets(session: SessionDep, who: WhoDep) -> dict:
    items = [b for b in budgets.list_budgets(session) if _visible(who, b["scope_dimension"], b["scope_value"])]
    return {"items": items}


@router.post("/budgets", status_code=status.HTTP_201_CREATED)
def create_budget(body: BudgetIn, session: SessionDep, who: WhoDep) -> dict:
    if bool(body.scope_dimension) != bool(body.scope_value):
        raise HTTPException(422, "scope_dimension and scope_value must be set together")
    if not _visible(who, body.scope_dimension, body.scope_value):
        raise HTTPException(403, "You can only create budgets for your own teams")
    budget = Budget(**body.model_dump())
    session.add(budget)
    session.commit()
    return budgets.evaluate(session, budget)


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget(budget_id: int, session: SessionDep, who: WhoDep) -> Response:
    budget = session.get(Budget, budget_id)
    if budget is None or not _visible(who, budget.scope_dimension, budget.scope_value):
        raise HTTPException(404, "Budget not found")
    session.delete(budget)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/alerts", dependencies=[Depends(require_unscoped)])  # alerts cover every team
def get_alerts(session: SessionDep, days: Days = 30) -> dict:
    return {"items": alerts.recent(session, days)}


@router.post("/alerts/evaluate", dependencies=[Depends(require_unscoped)])
def evaluate_alerts(session: SessionDep) -> dict:
    fired = alerts.evaluate(session)
    return {"fired": len(fired)}


@router.get("/pricing")
def get_pricing(session: SessionDep) -> dict:
    rows = session.scalars(select(PriceEntry).order_by(PriceEntry.provider, PriceEntry.model))
    return {
        "items": [
            {
                "provider": p.provider,
                "model": p.model,
                "input_per_mtok": p.input_per_mtok,
                "output_per_mtok": p.output_per_mtok,
                "cached_input_per_mtok": p.cached_input_per_mtok,
                "cache_write_per_mtok": p.cache_write_per_mtok,
                "effective_from": p.effective_from.isoformat(),
            }
            for p in rows
        ]
    }
