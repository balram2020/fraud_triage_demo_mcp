from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import (
    CostRepository,
    EscalationRepository,
    MessageRepository,
    SessionRepository,
    TraceRepository,
    get_db,
)
from app.graph.runtime import GraphRuntime
from app.observability.logger import get_logger, log_event
from app.observability.trace_export import export_graph_trace_json
from app.redis_controls.budgets import check_budget, update_budget
from app.redis_controls.idempotency import check_idempotency, store_idempotency_result
from app.redis_controls.locks import acquire_session_lock, release_session_lock
from app.redis_controls.rate_limits import check_rate_limit
from app.schemas.requests import ChatRequest
from app.schemas.responses import ChatResponse

router = APIRouter(prefix="/v1", tags=["chat"])
logger = get_logger()
graph_runtime = GraphRuntime()

session_repo = SessionRepository()
message_repo = MessageRepository()
trace_repo = TraceRepository()
cost_repo = CostRepository()
escalation_repo = EscalationRepository()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    """
    Full enterprise chat flow:
    Redis controls → Postgres persistence → LangGraph → response
    """
    request_id = request.request_id or f"req_{uuid.uuid4().hex[:12]}"
    trace_id = f"trace_{uuid.uuid4().hex[:12]}"
    session_id = request.session_id or f"sess_{uuid.uuid4().hex[:12]}"
    idempotency_key = request.idempotency_key or request_id

    log_event(
        logger,
        "chat_request_received",
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        session_id=session_id,
        request_id=request_id,
    )

    # Idempotency — retries should not create duplicate work
    cached = check_idempotency(idempotency_key)
    if cached:
        log_event(logger, "idempotency_hit", request_id=request_id)
        return ChatResponse(**cached)

    # Redis rate limit — protects the service from burst traffic
    allowed, rate_msg = check_rate_limit(request.tenant_id, request.user_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"{rate_msg} Wait 60 seconds for the rate-limit window to reset, or restart the redis container.",
        )

    estimated_cost = 0.006  # conservative estimate before graph run
    ok, budget_msg = check_budget(request.tenant_id, session_id, estimated_cost)
    if not ok:
        raise HTTPException(
            status_code=402,
            detail=f"{budget_msg} Increase SESSION_BUDGET_USD or TENANT_DAILY_BUDGET_USD in your environment.",
        )

    locked, lock_msg = acquire_session_lock(session_id)
    if not locked:
        raise HTTPException(
            status_code=409,
            detail=f"{lock_msg} Wait for the lock TTL to expire (~30s) or restart redis.",
        )

    try:
        # Postgres — create/update session and store user message
        session_repo.upsert_session(
            db,
            session_id=session_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            metadata=request.metadata,
        )
        message_repo.add_message(
            db,
            session_id=session_id,
            role="user",
            content=request.message,
            request_id=request_id,
        )

        # Invoke LangGraph A2A pipeline (OpenAI synthesis by default)
        try:
            result = graph_runtime.invoke(request.message, trace_id=trace_id, session_id=session_id)
        except RuntimeError as exc:
            if "OPENAI_API_KEY" in str(exc):
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            raise

        # ── Write JSON trace file to disk ──
        trace_state = {
            "trace_id": trace_id,
            "session_id": session_id,
            "user_request": request.message,
            **result,
        }
        trace_path, trace_err = export_graph_trace_json(trace_state)
        if trace_err:
            log_event(logger, "trace_export_failed", trace_id=trace_id, error=trace_err)
        elif trace_path:
            log_event(logger, "trace_exported", trace_id=trace_id, path=str(trace_path))

        # Persist trace events (engineering-facing)
        for event in result["trace_events"]:
            trace_repo.add_event(
                db,
                trace_id=trace_id,
                session_id=session_id,
                request_id=request_id,
                event_type=event.get("event", event.get("event_type", "unknown")),
                node_name=event.get("agent_or_skill", event.get("node_name", "unknown")),
                payload=event.get("meta", event.get("payload", {})),
            )

        # Persist cost ledger (business-facing)
        cost_repo.add_usage(
            db,
            trace_id=trace_id,
            session_id=session_id,
            tenant_id=request.tenant_id,
            model_name=result["model_name"],
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            cost_usd=result["total_cost_usd"],
        )

        # Store assistant message (user-facing)
        message_repo.add_message(
            db,
            session_id=session_id,
            role="assistant",
            content=result["answer"],
            request_id=request_id,
        )

        if result["escalated"]:
            escalation_repo.add_escalation(
                db,
                session_id=session_id,
                trace_id=trace_id,
                reason="Billing or policy review required",
                escalated_to="support_team",
            )

        update_budget(request.tenant_id, session_id, result["total_cost_usd"])

        response = ChatResponse(
            trace_id=trace_id,
            request_id=request_id,
            session_id=session_id,
            answer=result["answer"],
            skills_used=result["skills_used"],
            escalated=result["escalated"],
            total_cost_usd=result["total_cost_usd"],
            status="completed",
        )

        store_idempotency_result(idempotency_key, response.model_dump())
        log_event(
            logger,
            "chat_request_completed",
            trace_id=trace_id,
            session_id=session_id,
            total_cost_usd=result["total_cost_usd"],
        )
        return response
    finally:
        release_session_lock(session_id)
