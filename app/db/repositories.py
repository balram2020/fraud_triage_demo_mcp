from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.db.models import (
    CostUsageRecord,
    EscalationRecord,
    MessageRecord,
    SessionRecord,
    TraceEventRecord,
    utc_now,
)


class SessionRepository:
    def upsert_session(
        self,
        db: Session,
        *,
        session_id: str,
        tenant_id: str,
        user_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SessionRecord:
        row = db.get(SessionRecord, session_id)
        if row is None:
            row = SessionRecord(
                id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                status="active",
                metadata_json=json.dumps(metadata or {}),
            )
            db.add(row)
        else:
            row.updated_at = utc_now()
            if metadata:
                row.metadata_json = json.dumps(metadata)
        db.commit()
        db.refresh(row)
        return row


class MessageRepository:
    def add_message(
        self,
        db: Session,
        *,
        session_id: str,
        role: str,
        content: str,
        request_id: str,
    ) -> MessageRecord:
        row = MessageRecord(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            role=role,
            content=content,
            request_id=request_id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def list_by_session(self, db: Session, session_id: str) -> list[MessageRecord]:
        return (
            db.query(MessageRecord)
            .filter_by(session_id=session_id)
            .order_by(MessageRecord.created_at.asc())
            .all()
        )


class TraceRepository:
    def add_event(
        self,
        db: Session,
        *,
        trace_id: str,
        session_id: str,
        request_id: str,
        event_type: str,
        node_name: Optional[str],
        payload: dict[str, Any],
    ) -> TraceEventRecord:
        row = TraceEventRecord(
            id=f"evt_{uuid.uuid4().hex[:12]}",
            trace_id=trace_id,
            session_id=session_id,
            request_id=request_id,
            event_type=event_type,
            node_name=node_name,
            payload_json=json.dumps(payload),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def list_by_trace(self, db: Session, trace_id: str) -> list[TraceEventRecord]:
        return (
            db.query(TraceEventRecord)
            .filter_by(trace_id=trace_id)
            .order_by(TraceEventRecord.created_at.asc())
            .all()
        )


class CostRepository:
    def add_usage(
        self,
        db: Session,
        *,
        trace_id: str,
        session_id: str,
        tenant_id: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> CostUsageRecord:
        row = CostUsageRecord(
            id=f"cost_{uuid.uuid4().hex[:12]}",
            trace_id=trace_id,
            session_id=session_id,
            tenant_id=tenant_id,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


class EscalationRepository:
    def add_escalation(
        self,
        db: Session,
        *,
        session_id: str,
        trace_id: str,
        reason: str,
        escalated_to: str,
    ) -> EscalationRecord:
        row = EscalationRecord(
            id=f"esc_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            trace_id=trace_id,
            reason=reason,
            escalated_to=escalated_to,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
