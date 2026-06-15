"""Tenant daily and session budget counters — business guardrails."""

from __future__ import annotations

from app.redis_controls import (
    SESSION_BUDGET_USD,
    TENANT_DAILY_BUDGET_USD,
    get_redis,
)


def check_budget(tenant_id: str, session_id: str, estimated_cost: float) -> tuple[bool, str]:
    store = get_redis().client
    tenant_key = f"budget:tenant:{tenant_id}:daily"
    session_key = f"budget:session:{session_id}"

    tenant_spent = float(store.get(tenant_key) or "0")
    session_spent = float(store.get(session_key) or "0")

    if tenant_spent + estimated_cost > TENANT_DAILY_BUDGET_USD:
        return False, f"Tenant daily budget exceeded (${tenant_spent:.4f} spent)"
    if session_spent + estimated_cost > SESSION_BUDGET_USD:
        return False, f"Session budget exceeded (${session_spent:.4f} spent)"
    return True, "Budget OK"


def update_budget(tenant_id: str, session_id: str, cost: float) -> None:
    store = get_redis().client
    tenant_key = f"budget:tenant:{tenant_id}:daily"
    session_key = f"budget:session:{session_id}"
    store.set(tenant_key, str(float(store.get(tenant_key) or "0") + cost), ex=86400)
    store.set(session_key, str(float(store.get(session_key) or "0") + cost), ex=3600)
