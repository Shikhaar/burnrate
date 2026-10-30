from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate.models import Budget
from burnrate.services.analytics import month_window, naive_projection, total_cost


def evaluate(session: Session, budget: Budget) -> dict:
    w = month_window()
    filters = {budget.scope_dimension: budget.scope_value} if budget.scope_dimension else {}
    spent = total_cost(session, w, filters)
    projected = naive_projection(spent, w.end)
    utilization = spent / budget.monthly_limit_usd
    if utilization >= 1:
        status = "exceeded"
    elif utilization >= budget.alert_threshold or projected >= budget.monthly_limit_usd:
        status = "warning"
    else:
        status = "ok"
    return {
        "id": budget.id,
        "name": budget.name,
        "scope_dimension": budget.scope_dimension,
        "scope_value": budget.scope_value,
        "monthly_limit_usd": budget.monthly_limit_usd,
        "alert_threshold": budget.alert_threshold,
        "spent_usd": round(spent, 2),
        "projected_usd": round(projected, 2),
        "utilization": round(utilization, 4),
        "status": status,
    }


def list_budgets(session: Session) -> list[dict]:
    return [evaluate(session, b) for b in session.scalars(select(Budget).order_by(Budget.id))]
