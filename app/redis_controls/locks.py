"""
Session locks — a chat session is sequential even if infrastructure is parallel.
"""

from __future__ import annotations

from app.redis_controls import LOCK_TTL_SEC, get_redis


def acquire_session_lock(session_id: str) -> tuple[bool, str]:
    store = get_redis().client
    key = f"lock:session:{session_id}"
    acquired = store.set(key, "1", nx=True, ex=LOCK_TTL_SEC)
    if not acquired:
        return False, "Session busy — lock already held"
    return True, "Lock acquired"


def release_session_lock(session_id: str) -> None:
    get_redis().client.delete(f"lock:session:{session_id}")
