from __future__ import annotations

from fastapi import APIRouter

from app.db.session import get_postgres_status
from app.redis_controls import get_redis
from app.schemas.responses import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    postgres_ok, postgres_msg = get_postgres_status()
    redis_ok, redis_msg = get_redis().status()

    postgres_status = "ok" if postgres_ok else "error"
    redis_status = "ok" if redis_ok else "error"
    overall = "ok" if postgres_ok and redis_ok else "degraded"

    hint = None
    if not postgres_ok or not redis_ok:
        hints: list[str] = []
        if not postgres_ok:
            hints.append(f"Postgres: {postgres_msg}")
        if not redis_ok:
            hints.append(f"Redis: {redis_msg}")
        hint = " | ".join(hints)

    return HealthResponse(
        status=overall,
        postgres=postgres_status,
        redis=redis_status,
        hint=hint,
    )
