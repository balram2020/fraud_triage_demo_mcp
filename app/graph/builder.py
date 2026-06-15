"""A2A skills graph — simulated tool execution with Groq LLM synthesis."""

from __future__ import annotations

import os
import time
from typing import Any, TypedDict, Literal

import json
from dataclasses import dataclass, field
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from app.redis_controls.config import MAX_SKILL_CALLS, MAX_TOOL_CALLS_PER_SKILL, MAX_TOTAL_COST_USD, MAX_LATENCY_SECONDS

from app.schemas.requests import SkillContract, SkillTask, SkillPlan, SkillOutput, FinalAnswer, TraceEvent

from app.tools.simulated_tools import (
    get_order_status, lookup_transaction, check_issue_tracker, create_bug_ticket, 
    search_knowledge_base, rerank_results, check_refund_policy, check_escalation_policy)

from app.tools.agent_communication import (
    AgentHandoff, AgentMessage, AuditLog, AuditEntry,
    create_handoff, send_message, invoke_agent_as_tool,
    register_agent_tool,
)

# ── Register simulated tools so they can be called via agent-as-tool ──
register_agent_tool("get_order_status", get_order_status)
register_agent_tool("lookup_transaction", lookup_transaction)
register_agent_tool("check_issue_tracker", check_issue_tracker)
register_agent_tool("create_bug_ticket", create_bug_ticket)
register_agent_tool("search_knowledge_base", search_knowledge_base)
register_agent_tool("rerank_results", rerank_results)
register_agent_tool("check_refund_policy", check_refund_policy)
register_agent_tool("check_escalation_policy", check_escalation_policy)


DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "llama-3.3-70b-versatile")
COST_PER_SKILL = 0.002


def use_mock_llm() -> bool:
    return os.getenv("USE_MOCK_LLM", "").lower() in ("1", "true", "yes")


class AgentState(TypedDict, total=False):
    user_request: str
    message: str
    trace_id: str
    session_id: str
    blocked: bool
    block_reason: str
    skill_plan: dict[str, Any]
    validation_errors: list[str]
    final_answer: dict[str, Any]
    total_cost_usd: float
    skills: list[str]
    skill_outputs: list[dict[str, Any]] | dict[str, str]
    answer: str
    escalated: bool
    trace_events: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    llm_cost_usd: float
    used_mock_llm: bool
    # ── Agent communication fields ──
    audit_trail: list[dict[str, Any]]   # serialised AuditEntry list
    handoffs: list[dict[str, Any]]      # serialised AgentHandoff list


def _append_event(state: AgentState, node: str, event_type: str, payload: dict) -> None:
    state["trace_events"].append(
        {"node_name": node, "event_type": event_type, "payload": payload}
    )

def record_event(state: AgentState, event: str, agent_or_skill: str, meta: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "event": event,
        "trace_id": state["trace_id"],
        "session_id": state["session_id"],
        "agent_or_skill": agent_or_skill,
        "ts_ms": int(time.time() * 1000),
        "meta": meta or {},
    }
    # JSON-style print for clean teaching output
    print(json.dumps(payload, ensure_ascii=False))
    state["trace_events"].append(payload)
    
# ---------------------------
# Node 1: input guardrail
# ---------------------------
BLOCK_PATTERNS = [
    "ignore previous instructions",
    "reveal your system prompt",
    "developer message",
    "bypass policy",
]


def _get_audit_log(state: AgentState) -> AuditLog:
    """Reconstruct a live AuditLog from the serialised state entries."""
    log = AuditLog()
    for entry_dict in state.get("audit_trail") or []:
        log.append(AuditEntry(**{k: v for k, v in entry_dict.items()}))
    return log


def _flush_audit_log(state: AgentState, log: AuditLog) -> None:
    """Write the AuditLog back into state (serialised)."""
    state["audit_trail"] = log.to_dicts()


def input_guardrail(state: AgentState) -> AgentState:
    # Initialise communication fields on first entry
    state.setdefault("audit_trail", [])
    state.setdefault("handoffs", [])

    audit = _get_audit_log(state)

    # Production pattern: input guardrails block prompt-injection attempts early (before any planning).
    text = (state["user_request"] or "").lower()
    for pat in BLOCK_PATTERNS:
        if pat in text:
            state["blocked"] = True
            state["block_reason"] = f"blocked_prompt_injection_pattern: {pat}"
            record_event(state, "guardrail_blocked", "input_guardrail", meta={"pattern": pat})

            # Audit: record the block
            send_message(
                sender="input_guardrail", receiver="safe_response",
                message_type="guardrail_block",
                payload={"pattern": pat, "blocked": True},
                text=f"Blocked prompt-injection pattern: {pat}",
                audit_log=audit,
            )
            _flush_audit_log(state, audit)
            return state

    state["blocked"] = False
    record_event(state, "guardrail_passed", "input_guardrail")

    # Handoff: guardrail → skill_planner
    handoff = create_handoff(
        from_agent="input_guardrail",
        to_agent="skill_planner",
        task=state["user_request"],
        context={"trace_id": state["trace_id"], "session_id": state["session_id"]},
        priority="normal",
        audit_log=audit,
    )
    state["handoffs"].append(handoff.to_dict())
    _flush_audit_log(state, audit)
    return state

def safe_response_node(state: AgentState) -> AgentState:
    state["final_answer"] = FinalAnswer(
        answer="I can’t help with that request. Please ask a normal support question.",
        skills_used=[],
        confidence=0.9,
        escalated=False,
        reason=state.get("block_reason", "blocked by guardrail"),
    ).model_dump()
    state["escalated"] = False
    record_event(state, "safe_response", "safe_response")
    return state

def build_skill_registry() -> dict[str, SkillContract]:
    # Production pattern: skill contracts define what’s allowed (tools), what’s forbidden, limits, and fallback behavior.
    return {
        "orders_skill": SkillContract(
            name="orders_skill",
            description="Resolve order delivery, shipment delays, and returns.",
            allowed_tools=["get_order_status", "submit_return"],
            forbidden_actions=["issue_refund", "change_payment_method"],
            max_tool_calls=MAX_TOOL_CALLS_PER_SKILL,
            timeout_seconds=8,
            fallback_behavior="Request order_id to continue.",
            risk_level="low",
        ),
        "billing_skill": SkillContract(
            name="billing_skill",
            description="Resolve billing issues like failed payments, duplicate charges, and refunds.",
            allowed_tools=["lookup_transaction", "request_refund"],
            forbidden_actions=["commit_refund_without_policy_check"],
            max_tool_calls=MAX_TOOL_CALLS_PER_SKILL,
            timeout_seconds=8,
            fallback_behavior="Request transaction_id to continue.",
            risk_level="medium",
        ),
        "technical_skill": SkillContract(
            name="technical_skill",
            description="Triage app issues and create a bug ticket when needed.",
            allowed_tools=["check_issue_tracker", "create_bug_ticket"],
            forbidden_actions=["deploy_code", "access_production_db"],
            max_tool_calls=MAX_TOOL_CALLS_PER_SKILL,
            timeout_seconds=10,
            fallback_behavior="Collect repro steps and app version.",
            risk_level="low",
        ),
        "rag_skill": SkillContract(
            name="rag_skill",
            description="Answer FAQs using a knowledge base search + reranking.",
            allowed_tools=["search_knowledge_base", "rerank_results"],
            forbidden_actions=["hallucinate_policy"],
            max_tool_calls=MAX_TOOL_CALLS_PER_SKILL,
            timeout_seconds=6,
            fallback_behavior="Ask clarifying question.",
            risk_level="low",
        ),
        "policy_skill": SkillContract(
            name="policy_skill",
            description="Interpret refund/escalation policies and decide if human escalation is required.",
            allowed_tools=["check_refund_policy", "check_escalation_policy"],
            forbidden_actions=["override_policy"],
            max_tool_calls=MAX_TOOL_CALLS_PER_SKILL,
            timeout_seconds=6,
            fallback_behavior="Escalate to human.",
            risk_level="high",
        ),
    }


@dataclass
class RuntimeController:
    """
    Prompt instructions are *not* enforcement.
    Runtime wrappers enforce limits no matter what the LLM "wants".
    """

    trace_id: str
    session_id: str
    allowed_skills: set[str]
    max_skill_calls: int = MAX_SKILL_CALLS
    max_total_cost_usd: float = MAX_TOTAL_COST_USD
    max_latency_seconds: int = MAX_LATENCY_SECONDS

    # advanced (optional) production patterns:
    skill_timeout_seconds: dict[str, int] = field(default_factory=dict)
    failure_counts: dict[str, int] = field(default_factory=dict)
    breaker_open_skills: set[str] = field(default_factory=set)
    breaker_threshold: int = 2

    skill_calls_used: int = 0
    total_cost_usd: float = 0.0
    start_time: float = field(default_factory=time.time)

    def can_execute_skill(self, skill_name: str) -> tuple[bool, str]:
        # Production pattern: central enforcement gate. The executor must call this *every* time.
        if skill_name in self.breaker_open_skills:
            return False, "circuit_breaker_open"
        if skill_name not in self.allowed_skills:
            return False, "skill_not_allowed"
        if self.skill_calls_used >= self.max_skill_calls:
            return False, "max_skill_calls_exceeded"
        if self.total_cost_usd >= self.max_total_cost_usd:
            return False, "max_total_cost_usd_exceeded"
        if (time.time() - self.start_time) >= self.max_latency_seconds:
            return False, "max_latency_seconds_exceeded"
        return True, "ok"

    def estimate_cost_usd(self, skill_name: str) -> float:
        # simple, explainable estimation (avoid billing complexity in class)
        return {"policy_skill": 0.008, "billing_skill": 0.012}.get(skill_name, 0.01)

    def record_skill_start(self, state: AgentState, skill_name: str) -> None:
        self.skill_calls_used += 1
        record_event(
            state,
            "skill_start",
            agent_or_skill="runtime_controller",
            meta={"skill_name": skill_name, "skill_calls_used": self.skill_calls_used, "estimated_cost_usd": self.estimate_cost_usd(skill_name)},
        )

    def record_skill_result(self, state: AgentState, skill_name: str, cost_usd: float, latency_ms: int, ok: bool) -> None:
        self.total_cost_usd += float(cost_usd)
        state["total_cost_usd"] = self.total_cost_usd
        record_event(
            state,
            "skill_end",
            agent_or_skill="runtime_controller",
            meta={
                "skill_name": skill_name,
                "ok": ok,
                "latency_ms": latency_ms,
                "cost_usd": round(cost_usd, 6),
                "total_cost_usd": round(self.total_cost_usd, 6),
            },
        )
        if not ok:
            self.failure_counts[skill_name] = self.failure_counts.get(skill_name, 0) + 1
            if self.failure_counts[skill_name] >= self.breaker_threshold:
                self.breaker_open_skills.add(skill_name)
                record_event(
                    state,
                    "circuit_breaker_opened",
                    agent_or_skill="runtime_controller",
                    meta={"skill_name": skill_name, "failure_count": self.failure_counts[skill_name]},
                )


def _get_llm():
    # keep the demo runnable without network/API keys.
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    try:
        return ChatGroq(model=DEFAULT_MODEL, temperature=0, api_key=api_key)
    except Exception:
        return None



def skill_planner(state: AgentState) -> AgentState:
    registry = build_skill_registry()
    llm = _get_llm()

    # Production pattern: planner returns a structured plan (SkillPlan) instead of free-form text.
    if llm is not None:
        prompt = (
            "You are a production skill planner.\n"
            "Return a JSON SkillPlan.\n"
            "Rules:\n"
            "- Choose at most 3 skills\n"
            "- For mixed intents, select multiple skills\n"
            "- If billing/payment/refund risk is present, include policy_skill\n"
            f"Available skills: {list(registry.keys())}\n"
            f"User request: {state['user_request']}"
        )
        try:
            structured = llm.with_structured_output(SkillPlan, method="function_calling")  # type: ignore[call-arg,attr-defined]
            plan_obj: SkillPlan = structured.invoke(prompt)
            state["skill_plan"] = plan_obj.model_dump()
            record_event(state, "skill_plan_generated", "skill_planner", meta=state["skill_plan"])
            return state
        except Exception as e:
            record_event(state, "skill_plan_llm_failed", "skill_planner", meta={"error": str(e)})

    # fallback heuristic plan (always runnable)
    txt = state["user_request"].lower()
    tasks: list[SkillTask] = []
    if "order" in txt or "arrived" in txt or "shipping" in txt:
        tasks.append(SkillTask(skill_name="orders_skill", task="Check order status and next steps.", reason="Delivery issue.", priority="high"))
    if "crash" in txt or "crashes" in txt or "bug" in txt:
        tasks.append(SkillTask(skill_name="technical_skill", task="Triage crash and create ticket if needed.", reason="Technical issue reported.", priority="high"))
    if "charged" in txt or "debited" in txt or "payment" in txt:
        tasks.append(SkillTask(skill_name="billing_skill", task="Check payment/charge status and duplicates.", reason="Billing risk.", priority="high"))
        tasks.append(SkillTask(skill_name="policy_skill", task="Confirm refund/escalation policy.", reason="Billing high priority requires policy.", priority="high"))
    if "refund policy" in txt or "refund" in txt:
        tasks = [SkillTask(skill_name="policy_skill", task="Explain refund policy clearly.", reason="Policy request.", priority="normal")]
    if "business hours" in txt or "hours" in txt:
        tasks = [SkillTask(skill_name="rag_skill", task="Find business hours from KB.", reason="FAQ.", priority="normal")]
    if not tasks:
        tasks = [SkillTask(skill_name="rag_skill", task="Search KB and answer.", reason="General FAQ.", priority="normal")]

    plan_obj = SkillPlan(tasks=tasks[:3], requires_human=False, reason="Heuristic planner fallback.")
    state["skill_plan"] = plan_obj.model_dump()
    record_event(state, "skill_plan_generated", "skill_planner", meta=state["skill_plan"])
    return state


# ---------------------------
# Node 3: deterministic validator
# ---------------------------
def plan_validator_node(state: AgentState) -> AgentState:
    registry = build_skill_registry()
    plan = SkillPlan.model_validate(state.get("skill_plan") or {"tasks": [], "requires_human": False, "reason": "missing"})
    errors: list[str] = []

    # Production pattern: deterministic validation (max skills, allowed skills, required policy, etc.)
    if len(plan.tasks) > 3:
        errors.append("max 3 skills")

    seen: set[str] = set()
    for t in plan.tasks:
        if t.skill_name not in registry:
            errors.append(f"skill does not exist: {t.skill_name}")
        if t.skill_name in seen:
            errors.append(f"duplicate skill not allowed: {t.skill_name}")
        seen.add(t.skill_name)

    has_high_billing = any(t.skill_name == "billing_skill" and t.priority == "high" for t in plan.tasks)
    has_policy = any(t.skill_name == "policy_skill" for t in plan.tasks)
    if has_high_billing and not has_policy:
        errors.append("billing high-priority requires policy_skill")

    # Example of "allowed skills" constraint
    allowed_skills = set(registry.keys())
    for t in plan.tasks:
        if t.skill_name not in allowed_skills:
            errors.append(f"skill not allowed: {t.skill_name}")

    state["validation_errors"] = errors
    record_event(state, "plan_validated", "plan_validator", meta={"ok": not errors, "errors": errors})
    return state

def clarify_or_escalate_node(state: AgentState) -> AgentState:
    errors = state.get("validation_errors") or []
    state["escalated"] = True
    state["final_answer"] = FinalAnswer(
        answer="I can’t safely execute that plan. I need clarification or a human review.\n"
        + "Validation errors:\n"
        + "\n".join([f"- {e}" for e in errors]),
        skills_used=[],
        confidence=0.4,
        escalated=True,
        reason="Plan failed deterministic validation.",
    ).model_dump()
    record_event(state, "clarify_or_escalate", "clarify_or_escalate", meta={"errors": errors})
    return state

# ---------------------------
# Node 5: skill executor (with runtime controls)
# ---------------------------
def execute_skill(task: SkillTask) -> SkillOutput:
    # Kept simple and deterministic; skills call simulated internal tools.
    if task.skill_name == "orders_skill":
        status = get_order_status("ord_1234")
        return SkillOutput(
            skill_name="orders_skill",
            status="resolved",
            answer=f"Order ord_1234 is {status['status']} (ETA {status['eta_days']} day(s)).",
            confidence=0.74,
            tools_used=["get_order_status"],
            requires_human=False,
            risk_level="low",
        )

    if task.skill_name == "billing_skill":
        tx1 = lookup_transaction("txn_7781")
        tx2 = lookup_transaction("txn_7782")
        suspected_double = tx1["status"] == "settled" and tx2["status"] == "settled"
        return SkillOutput(
            skill_name="billing_skill",
            status="resolved" if suspected_double else "needs_more_info",
            answer=f"Transactions checked: txn_7781={tx1['status']}, txn_7782={tx2['status']}.",
            confidence=0.7 if suspected_double else 0.55,
            tools_used=["lookup_transaction", "lookup_transaction"],
            requires_human=False,
            risk_level="medium",
        )

    if task.skill_name == "technical_skill":
        hits = check_issue_tracker("checkout crash")
        tools = ["check_issue_tracker"]
        ticket = None
        if (hits.get("matching_issues") or 0) == 0:
            ticket = create_bug_ticket("Crash during checkout", priority="high")
            tools.append("create_bug_ticket")
        return SkillOutput(
            skill_name="technical_skill",
            status="resolved",
            answer=f"Issue tracker: {hits}. Ticket: {ticket}.",
            confidence=0.65,
            tools_used=tools,
            requires_human=False,
            risk_level="low",
        )

    if task.skill_name == "rag_skill":
        results = search_knowledge_base(task.task)
        top = rerank_results(results)
        return SkillOutput(
            skill_name="rag_skill",
            status="resolved",
            answer="; ".join([f"{d['title']} — {d['snippet']}" for d in top]),
            confidence=0.66,
            tools_used=["search_knowledge_base", "rerank_results"],
            requires_human=False,
            risk_level="low",
        )

    if task.skill_name == "policy_skill":
        policy = check_refund_policy(amount=49.0)
        escalation = check_escalation_policy(issue_type="charged twice")
        requires_human = bool(escalation.get("requires_human"))
        return SkillOutput(
            skill_name="policy_skill",
            status="escalated" if requires_human else "resolved",
            answer=f"Refund policy: {policy}. Escalation policy: {escalation}.",
            confidence=0.72,
            tools_used=["check_refund_policy", "check_escalation_policy"],
            requires_human=requires_human,
            risk_level="high",
        )

    return SkillOutput(
        skill_name=str(task.skill_name),
        status="failed",
        answer="Unsupported skill.",
        confidence=0.0,
        tools_used=[],
        requires_human=True,
        risk_level="high",
    )


def route_after_validation(state: AgentState) -> Literal["safe_response", "clarify_or_escalate", "skill_executor"]:
    if state.get("blocked"):
        return "safe_response"
    if state.get("validation_errors"):
        return "clarify_or_escalate"
    return "skill_executor"

def skill_executor_node(state: AgentState) -> AgentState:
    registry = build_skill_registry()
    plan = SkillPlan.model_validate(state["skill_plan"])
    audit = _get_audit_log(state)

    # Handoff: planner → executor
    handoff = create_handoff(
        from_agent="skill_planner",
        to_agent="skill_executor",
        task="Execute planned skills",
        context={"plan_tasks": [t.skill_name for t in plan.tasks]},
        priority="high" if any(t.priority == "high" for t in plan.tasks) else "normal",
        audit_log=audit,
    )
    state["handoffs"].append(handoff.to_dict())

    # Production pattern: runtime controller enforces limits (skills, cost, latency) regardless of prompts.
    runtime = RuntimeController(
        trace_id=state["trace_id"],
        session_id=state["session_id"],
        allowed_skills=set(registry.keys()),
        max_skill_calls=MAX_SKILL_CALLS,
        max_total_cost_usd=MAX_TOTAL_COST_USD,
        max_latency_seconds=MAX_LATENCY_SECONDS,
        skill_timeout_seconds={k: v.timeout_seconds for k, v in registry.items()},
    )

    outputs: list[SkillOutput] = []
    for task in plan.tasks:
        ok, reason = runtime.can_execute_skill(task.skill_name)
        if not ok:
            record_event(state, "skill_rejected", "runtime_controller", meta={"skill_name": task.skill_name, "reason": reason})
            # Audit: rejected skill message
            send_message(
                sender="runtime_controller", receiver=task.skill_name,
                message_type="skill_rejected",
                payload={"reason": reason},
                text=f"Skill {task.skill_name} rejected: {reason}",
                audit_log=audit,
            )
            outputs.append(
                SkillOutput(
                    skill_name=task.skill_name,
                    status="failed",
                    answer=f"Skill blocked by runtime: {reason}. Fallback: {registry[task.skill_name].fallback_behavior}",
                    confidence=0.2,
                    tools_used=[],
                    requires_human=True,
                    risk_level="high",
                )
            )
            continue

        runtime.record_skill_start(state, task.skill_name)

        t0 = time.time()
        simulated_timeout_s = runtime.skill_timeout_seconds.get(task.skill_name, 8)

        try:
            # simulate bounded work (without slowing class)
            time.sleep(0.05)
            if (time.time() - t0) > simulated_timeout_s:
                raise TimeoutError("skill_timeout")

            out = execute_skill(task)
            outputs.append(out)
            latency_ms = int((time.time() - t0) * 1000)
            runtime.record_skill_result(state, task.skill_name, cost_usd=runtime.estimate_cost_usd(task.skill_name), latency_ms=latency_ms, ok=True)

            # Structured message: skill → synthesis (agent-as-tool result)
            send_message(
                sender=task.skill_name, receiver="synthesis",
                message_type="skill_result",
                payload=out.model_dump(),
                text=f"{task.skill_name} completed: {out.status}",
                audit_log=audit,
            )
        except Exception as e:
            latency_ms = int((time.time() - t0) * 1000)
            runtime.record_skill_result(state, task.skill_name, cost_usd=0.0, latency_ms=latency_ms, ok=False)
            send_message(
                sender=task.skill_name, receiver="synthesis",
                message_type="skill_failed",
                payload={"error": str(e), "skill_name": task.skill_name},
                text=f"{task.skill_name} failed: {e}",
                audit_log=audit,
            )
            outputs.append(
                SkillOutput(
                    skill_name=task.skill_name,
                    status="failed",
                    answer=f"Skill failed: {type(e).__name__}. Fallback: {registry[task.skill_name].fallback_behavior}",
                    confidence=0.2,
                    tools_used=[],
                    requires_human=True,
                    risk_level="high",
                )
            )

    state["skill_outputs"] = [o.model_dump() for o in outputs]
    record_event(state, "skills_executed", "skill_executor", meta={"count": len(outputs)})
    _flush_audit_log(state, audit)
    return state


def _mock_synthesize(state: AgentState) -> str:
    parts = list(state.get("skill_outputs", {}).values())
    answer = " ".join(parts) if parts else "How can I help you today?"
    if "billing_skill" in state.get("skills", []):
        answer += " I have flagged this for billing review."
    return answer


def _extract_token_usage(message: Any) -> tuple[int, int]:
    usage = getattr(message, "usage_metadata", None) or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    if input_tokens or output_tokens:
        return input_tokens, output_tokens

    meta = getattr(message, "response_metadata", None) or {}
    token_usage = meta.get("token_usage") or {}
    return (
        int(token_usage.get("prompt_tokens") or 0),
        int(token_usage.get("completion_tokens") or 0),
    )


def _estimate_llm_cost(input_tokens: int, output_tokens: int) -> float:
    if input_tokens or output_tokens:
        return round(input_tokens * 0.00000015 + output_tokens * 0.0000006, 6)
    return 0.002


def _groq_synthesize(state: AgentState) -> tuple[str, int, int, float]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is required. Set it in .env or export it. "
            "For offline deterministic replies, set USE_MOCK_LLM=true."
        )

    llm = ChatGroq(model=DEFAULT_MODEL, temperature=0, api_key=api_key)
    skill_lines = "\n".join(
        f"- {name}: {output}" for name, output in state.get("skill_outputs", {}).items()
    )
    system_prompt = (
        "You are a concise customer support assistant. "
        "Use only the skill findings below to answer the customer. "
        "Do not invent facts that are not supported by the findings."
    )
    user_prompt = (
        f"Customer message:\n{state['message']}\n\n"
        f"Skill findings:\n{skill_lines or '- No skill data'}\n\n"
        "Write a brief, helpful reply."
    )
    response = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    )
    answer = str(response.content).strip()
    input_tokens, output_tokens = _extract_token_usage(response)
    return answer, input_tokens, output_tokens, _estimate_llm_cost(input_tokens, output_tokens)


# ---------------------------
# Node 6: synthesis
# ---------------------------
def synthesis_node(state: AgentState) -> AgentState:
    # Production pattern: synthesis is separate so execution outputs remain auditable and testable.
    audit = _get_audit_log(state)
    outputs = [SkillOutput.model_validate(o) for o in (state.get("skill_outputs") or [])]
    escalated = any(o.requires_human or o.risk_level == "high" for o in outputs)
    skills_used = [o.skill_name for o in outputs]
    confidence = round(sum(o.confidence for o in outputs) / max(1, len(outputs)), 2)

    # Handoff: executor → synthesis
    handoff = create_handoff(
        from_agent="skill_executor",
        to_agent="synthesis",
        task="Synthesise final answer from skill outputs",
        context={"skills_used": skills_used, "escalated": escalated},
        priority="high" if escalated else "normal",
        audit_log=audit,
    )
    state["handoffs"].append(handoff.to_dict())

    # Try LLM synthesis first, fall back to structured summary
    answer_text = ""
    input_tokens = 0
    output_tokens = 0
    llm_cost = 0.0

    llm = _get_llm()
    if llm is not None and not use_mock_llm():
        try:
            skill_lines = "\n".join(
                f"- {o.skill_name}: {o.answer}" for o in outputs
            )
            system_prompt = (
                "You are a concise customer support assistant. "
                "Use only the skill findings below to answer the customer. "
                "Do not invent facts that are not supported by the findings."
            )
            user_prompt = (
                f"Customer message:\n{state['message']}\n\n"
                f"Skill findings:\n{skill_lines or '- No skill data'}\n\n"
                "Write a brief, helpful reply."
            )
            response = llm.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
            answer_text = str(response.content).strip()
            input_tokens, output_tokens = _extract_token_usage(response)
            llm_cost = _estimate_llm_cost(input_tokens, output_tokens)
        except Exception as e:
            record_event(state, "synthesis_llm_failed", "synthesis", meta={"error": str(e)})

    # Fallback: build a clean answer from skill outputs
    if not answer_text:
        if outputs:
            parts = []
            for o in outputs:
                parts.append(o.answer)
            answer_text = " ".join(parts)
            if any(o.skill_name == "billing_skill" for o in outputs):
                answer_text += " I have flagged this for billing review."
        else:
            answer_text = "I'm sorry, I couldn't find relevant information. How else can I help you?"

    final = FinalAnswer(
        answer=answer_text,
        skills_used=skills_used,
        confidence=float(confidence),
        escalated=escalated,
        reason="Escalated due to high-risk or human-required outputs." if escalated else "Completed with runtime-enforced constraints.",
    )
    state["final_answer"] = final.model_dump()
    state["answer"] = final.answer
    state["escalated"] = escalated
    state["input_tokens"] = state.get("input_tokens", 0) + input_tokens
    state["output_tokens"] = state.get("output_tokens", 0) + output_tokens
    state["llm_cost_usd"] = state.get("llm_cost_usd", 0.0) + llm_cost

    # Final structured message: synthesis → user
    send_message(
        sender="synthesis", receiver="user",
        message_type="final_answer",
        payload=final.model_dump(),
        text=answer_text[:200],
        audit_log=audit,
    )

    record_event(state, "final_answer_ready", "synthesis", meta={"escalated": escalated, "skills_used": skills_used})
    _flush_audit_log(state, audit)
    return state


def route_after_guardrail(state: AgentState) -> str:
    return END if state.get("answer") else "skill_planner"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("input_guardrail", input_guardrail)
    graph.add_node("safe_response", safe_response_node)
    graph.add_node("skill_planner", skill_planner)
    graph.add_node("plan_validator", plan_validator_node)
    graph.add_node("clarify_or_escalate", clarify_or_escalate_node)
    graph.add_node("skill_executor", skill_executor_node)
    graph.add_node("synthesis", synthesis_node)

    graph.set_entry_point("input_guardrail")
    graph.add_conditional_edges("input_guardrail", route_after_guardrail, {
        "safe_response": "safe_response",
        "skill_planner": "skill_planner",
    })
    graph.add_edge("skill_planner", "plan_validator")

    graph.add_conditional_edges("plan_validator", route_after_validation, {
        "safe_response": "safe_response",
        "clarify_or_escalate": "clarify_or_escalate",
        "skill_executor": "skill_executor",
    })
    graph.add_edge("safe_response", END)
    graph.add_edge("clarify_or_escalate", END)
    graph.add_edge("skill_executor", "synthesis")
    graph.add_edge("synthesis", END)
    return graph.compile()
