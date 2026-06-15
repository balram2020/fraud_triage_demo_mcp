from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import MessageRepository, get_db
from app.schemas.responses import MessageOut

router = APIRouter(prefix="/v1", tags=["sessions"])
message_repo = MessageRepository()


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def list_session_messages(session_id: str, db: Session = Depends(get_db)) -> list[MessageOut]:
    rows = message_repo.list_by_session(db, session_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No messages found for session")
    return [
        MessageOut(
            id=row.id,
            session_id=row.session_id,
            role=row.role,
            content=row.content,
            request_id=row.request_id,
            created_at=row.created_at,
        )
        for row in rows
    ]
