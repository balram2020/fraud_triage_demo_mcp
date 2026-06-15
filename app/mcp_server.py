"""
Fraud Triage MCP Server — exposes business skills as MCP tools.

Design principles from Week-4-mcp reference:
  1. Every tool validates input with a Pydantic schema before executing.
  2. Every tool returns a structured success or error envelope — never raw exceptions.
  3. Tool docstrings document "Use when" / "Do NOT use when" so the LLM can
     decide which tool to call and when to skip one entirely.
  4. MCP Prompts are registered as reusable templates for common answer formats.

Transport:
  - STDIO (default): `python -m app.mcp_server`
  - SSE:             `python -m app.mcp_server --transport sse --port 8001`
"""
from __future__ import annotations

import sys
import random
from mcp.server.fastmcp import FastMCP

from app.tools.simulated_tools import (
    get_order_status as _get_order_status,
    submit_return as _submit_return,
    lookup_transaction as _lookup_transaction,
    request_refund as _request_refund,
    check_issue_tracker as _check_issue_tracker,
    create_bug_ticket as _create_bug_ticket,
    search_knowledge_base as _search_knowledge_base,
    check_refund_policy as _check_refund_policy,
    check_escalation_policy as _check_escalation_policy,
)
from app.tools.tool_schemas import (
    GetOrderStatusInput, SubmitReturnInput,
    LookupTransactionInput, RequestRefundInput,
    CheckIssueTrackerInput, CreateBugTicketInput,
    SearchKnowledgeBaseInput,
    CheckRefundPolicyInput, CheckEscalationPolicyInput,
)
from app.tools.tool_contracts import success_response, error_response

# ── Server ──────────────────────────────────────────────────────────────────
mcp = FastMCP("Fraud Triage — Simulated Business Tools")


# ── ORDER TOOLS ─────────────────────────────────────────────────────────────

@mcp.tool()
def get_order_status(order_id: str) -> dict:
    """
    Get the current delivery status of a customer order.

    Use when:
    - The user asks about their order status, delivery, or shipping
    - The user provides an order ID (e.g. ord_1234)

    Do NOT use when:
    - The user asks about billing or charges (use lookup_transaction instead)
    - No order ID has been provided
    """
    try:
        validated = GetOrderStatusInput(order_id=order_id)
    except Exception as exc:
        return error_response(
            error_type="validation_error",
            message="Invalid order ID",
            recoverable=True,
            fallback_suggestion="Ask the user to provide their order ID (e.g. ord_1234)",
            details={"exception": str(exc)},
        )
    result = _get_order_status(validated.order_id)
    return success_response(data=result, source="order_management_system", cost_usd=0.001, latency_ms=50)


@mcp.tool()
def submit_return(order_id: str, reason: str) -> dict:
    """
    Submit a return request for a delivered order.

    Use when:
    - The user wants to return an item and provides an order ID and reason
    - The order has been delivered (confirmed by get_order_status)

    Do NOT use when:
    - The order is still in transit (status != 'delivered')
    - No reason has been provided by the user
    """
    try:
        validated = SubmitReturnInput(order_id=order_id, reason=reason)
    except Exception as exc:
        return error_response(
            error_type="validation_error",
            message="Invalid return request. Reason must be at least 5 characters.",
            recoverable=True,
            fallback_suggestion="Ask the user to describe why they want to return the item",
            details={"exception": str(exc)},
        )
    result = _submit_return(validated.order_id, validated.reason)
    return success_response(data=result, source="returns_system", cost_usd=0.002, latency_ms=80)


# ── BILLING TOOLS ────────────────────────────────────────────────────────────

@mcp.tool()
def lookup_transaction(transaction_id: str) -> dict:
    """
    Look up a billing transaction by its ID.

    Use when:
    - The user asks about a charge, payment, or transaction
    - The user reports being double-charged or seeing an unknown charge

    Do NOT use when:
    - The user only asks about order status or delivery (use get_order_status)
    """
    try:
        validated = LookupTransactionInput(transaction_id=transaction_id)
    except Exception as exc:
        return error_response(
            error_type="validation_error",
            message="Invalid transaction ID",
            recoverable=True,
            fallback_suggestion="Ask the user for their transaction or payment ID",
            details={"exception": str(exc)},
        )
    result = _lookup_transaction(validated.transaction_id)
    return success_response(data=result, source="billing_system", cost_usd=0.001, latency_ms=40)


@mcp.tool()
def request_refund(transaction_id: str, amount: float) -> dict:
    """
    Request a refund for a specific transaction amount.

    Use when:
    - The user is eligible for a refund (confirmed via check_refund_policy)
    - A valid transaction ID and refund amount have been provided

    Do NOT use when:
    - Eligibility has not been checked with check_refund_policy
    - The amount exceeds $50 (route to manual_review instead)
    - A chargeback or fraud is suspected (escalate to risk_ops)
    """
    try:
        validated = RequestRefundInput(transaction_id=transaction_id, amount=amount)
    except Exception as exc:
        return error_response(
            error_type="validation_error",
            message="Invalid refund request. Amount must be greater than 0.",
            recoverable=True,
            fallback_suggestion="Confirm the exact amount the user was incorrectly charged",
            details={"exception": str(exc)},
        )
    result = _request_refund(validated.transaction_id, validated.amount)
    return success_response(data=result, source="billing_system", cost_usd=0.002, latency_ms=90)


# ── TECHNICAL TOOLS ──────────────────────────────────────────────────────────

@mcp.tool()
def check_issue_tracker(keyword: str) -> dict:
    """
    Search the internal issue tracker for known bugs or incidents.

    Use when:
    - The user reports a technical problem (app crash, checkout error, login failure)
    - You want to check if the issue is a known bug before creating a new ticket

    Do NOT use when:
    - The issue is billing or shipping related
    - The keyword is too vague (single word like 'error')
    """
    try:
        validated = CheckIssueTrackerInput(keyword=keyword)
    except Exception as exc:
        return error_response(
            error_type="validation_error",
            message="Invalid keyword for issue tracker search",
            recoverable=True,
            details={"exception": str(exc)},
        )
    result = _check_issue_tracker(validated.keyword)
    return success_response(data=result, source="issue_tracker", cost_usd=0.001, latency_ms=30)


@mcp.tool()
def create_bug_ticket(summary: str, priority: str = "medium") -> dict:
    """
    Create a new bug ticket in the issue tracker.

    Use when:
    - The issue is not a known bug (verified via check_issue_tracker)
    - A clear summary of the problem has been collected from the user

    Do NOT use when:
    - A matching open issue already exists in the tracker
    - The summary is too short or vague (less than 10 characters)
    """
    try:
        validated = CreateBugTicketInput(summary=summary, priority=priority)
    except Exception as exc:
        return error_response(
            error_type="validation_error",
            message="Invalid bug ticket. Summary must be at least 10 characters.",
            recoverable=True,
            fallback_suggestion="Ask the user to describe the bug in more detail",
            details={"exception": str(exc)},
        )
    result = _create_bug_ticket(validated.summary, validated.priority)
    return success_response(data=result, source="issue_tracker", cost_usd=0.002, latency_ms=70)


# ── RAG / KNOWLEDGE TOOLS ────────────────────────────────────────────────────

@mcp.tool()
def search_knowledge_base(query: str) -> dict:
    """
    Search the internal knowledge base for policy and support documentation.

    Use when:
    - The user asks a general question that could be answered by documentation
    - You need to check refund, shipping, or support policies before taking an action

    Do NOT use when:
    - The user needs real-time order or transaction status
    - The query is about a specific personal account or order
    """
    try:
        validated = SearchKnowledgeBaseInput(query=query)
    except Exception as exc:
        return error_response(
            error_type="validation_error",
            message="Invalid search query",
            recoverable=True,
            details={"exception": str(exc)},
        )
    results = _search_knowledge_base(validated.query)
    if not results or all(r.get("score", 0) == 0 for r in results):
        return error_response(
            error_type="no_results",
            message="No knowledge base articles matched the query",
            recoverable=True,
            fallback_suggestion="Try different keywords or escalate to a human agent",
        )
    return success_response(
        data={"results": results, "count": len(results)},
        source="knowledge_base",
        cost_usd=0.001,
        latency_ms=60,
    )


# ── POLICY TOOLS ─────────────────────────────────────────────────────────────

@mcp.tool()
def check_refund_policy(amount: float) -> dict:
    """
    Check if a refund amount is eligible for automatic or manual processing.

    Use when:
    - Before issuing a refund with request_refund
    - To determine the correct refund route (auto vs manual_review)

    Do NOT use when:
    - The refund is part of a fraud or chargeback case (use check_escalation_policy)
    """
    try:
        validated = CheckRefundPolicyInput(amount=amount)
    except Exception as exc:
        return error_response(
            error_type="validation_error",
            message="Invalid amount for policy check. Must be greater than 0.",
            recoverable=True,
            details={"exception": str(exc)},
        )
    result = _check_refund_policy(validated.amount)
    return success_response(data=result, source="policy_engine", cost_usd=0.0, latency_ms=5)


@mcp.tool()
def check_escalation_policy(issue_type: str) -> dict:
    """
    Determine whether an issue requires human escalation and which team handles it.

    Use when:
    - Handling sensitive issues like chargebacks, fraud, or double charges
    - Deciding if a case should be routed to risk_ops, billing_ops, or support

    Do NOT use when:
    - The issue is routine and can be resolved with existing tools
    """
    try:
        validated = CheckEscalationPolicyInput(issue_type=issue_type)
    except Exception as exc:
        return error_response(
            error_type="validation_error",
            message="Invalid issue type for escalation check",
            recoverable=True,
            details={"exception": str(exc)},
        )
    result = _check_escalation_policy(validated.issue_type)
    return success_response(data=result, source="escalation_policy_engine", cost_usd=0.0, latency_ms=5)


# ── MCP PROMPTS ──────────────────────────────────────────────────────────────

@mcp.prompt()
def summarize_order_status(order_id: str, status: str, eta_days: int) -> str:
    """Summarize an order status update in a customer-friendly message."""
    return (
        f"Order {order_id} is currently **{status}**.\n"
        f"Estimated delivery: {eta_days} day(s) from now.\n"
        "Please let me know if you have any further questions."
    )


@mcp.prompt()
def explain_refund_decision(eligible: bool, route: str, max_days: int) -> str:
    """Explain a refund eligibility decision to a customer."""
    if eligible and route == "auto_refund":
        return (
            f"Great news! Your refund is eligible for **automatic processing** "
            f"and will be returned within {max_days} business days."
        )
    elif eligible and route == "manual_review":
        return (
            f"Your refund requires **manual review** by our billing team. "
            f"You can expect a decision within {max_days} business days."
        )
    return "Unfortunately, your refund request is not eligible based on our current policy."


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--transport" in sys.argv and "sse" in sys.argv:
        port = 8001
        if "--port" in sys.argv:
            try:
                port = int(sys.argv[sys.argv.index("--port") + 1])
            except (ValueError, IndexError):
                pass
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        mcp.run()
