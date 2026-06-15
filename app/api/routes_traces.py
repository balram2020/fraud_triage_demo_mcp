from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import TraceRepository, get_db
from app.schemas.responses import TraceEventOut

router = APIRouter(prefix="/v1", tags=["traces"])
trace_repo = TraceRepository()


@router.get("/traces/{trace_id}", response_model=list[TraceEventOut])
def list_trace_events(trace_id: str, db: Session = Depends(get_db)) -> list[TraceEventOut]:
    rows = trace_repo.list_by_trace(db, trace_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No trace events found")
    return [
        TraceEventOut(
            id=row.id,
            trace_id=row.trace_id,
            session_id=row.session_id,
            request_id=row.request_id,
            event_type=row.event_type,
            node_name=row.node_name,
            payload_json=row.payload_json,
            created_at=row.created_at,
        )
        for row in rows
    ]
