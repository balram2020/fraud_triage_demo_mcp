from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ChatResponse(BaseModel):
    trace_id: str
    request_id: str
    session_id: str
    answer: str
    skills_used: list[str]
    escalated: bool
    total_cost_usd: float
    status: str


class HealthResponse(BaseModel):
    status: str
    postgres: str
    redis: str
    hint: Optional[str] = None


class MessageOut(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    request_id: Optional[str]
    created_at: datetime


class TraceEventOut(BaseModel):
    id: str
    trace_id: str
    session_id: str
    request_id: str
    event_type: str
    node_name: Optional[str]
    payload_json: Optional[str]
    created_at: datetime
