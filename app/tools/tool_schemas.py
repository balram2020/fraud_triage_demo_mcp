"""Pydantic input schemas for every MCP tool.

The schema IS the API documentation the LLM reads at runtime.
Tight schemas = less guessing by the model.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# --- Orders ---
class GetOrderStatusInput(BaseModel):
    order_id: str = Field(..., description="The unique order ID, e.g. ord_1234")


class SubmitReturnInput(BaseModel):
    order_id: str = Field(..., description="The order ID to be returned")
    reason: str = Field(..., min_length=5, description="Reason for the return")


# --- Billing ---
class LookupTransactionInput(BaseModel):
    transaction_id: str = Field(..., description="The unique transaction ID, e.g. txn_9876")


class RequestRefundInput(BaseModel):
    transaction_id: str = Field(..., description="Transaction ID to refund")
    amount: float = Field(..., gt=0, description="Refund amount in USD, must be > 0")


# --- Technical ---
class CheckIssueTrackerInput(BaseModel):
    keyword: str = Field(..., description="Keyword or phrase to search in the issue tracker")


class CreateBugTicketInput(BaseModel):
    summary: str = Field(..., min_length=10, description="Clear summary of the bug")
    priority: Literal["low", "medium", "high"] = Field(
        default="medium", description="Priority level of the bug"
    )


# --- RAG / Knowledge ---
class SearchKnowledgeBaseInput(BaseModel):
    query: str = Field(..., description="Natural language search query")


# --- Policy ---
class CheckRefundPolicyInput(BaseModel):
    amount: float = Field(..., gt=0, description="Refund amount in USD to check policy for")


class CheckEscalationPolicyInput(BaseModel):
    issue_type: str = Field(
        ...,
        description=(
            "Type of issue to check escalation for. "
            "Examples: 'chargeback', 'fraud', 'double charge', 'shipping delay'"
        ),
    )
