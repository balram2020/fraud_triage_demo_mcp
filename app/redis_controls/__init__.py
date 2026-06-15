from __future__ import annotations

import os
from typing import Optional

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "10"))
RATE_LIMIT_WINDOW_SEC = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))
TENANT_DAILY_BUDGET_USD = float(os.getenv("TENANT_DAILY_BUDGET_USD", "1.0"))
SESSION_BUDGET_USD = float(os.getenv("SESSION_BUDGET_USD", "0.05"))
LOCK_TTL_SEC = int(os.getenv("LOCK_TTL_SEC", "30"))
IDEMPOTENCY_TTL_SEC = int(os.getenv("IDEMPOTENCY_TTL_SEC", "3600"))


def redis_connection_hint(error: Exception | None = None) -> str:
    """Actionable guidance when Redis is unreachable."""
    detail = str(error).lower() if error else ""
    if "connection refused" in detail:
        return (
            "Redis connection refused. Start Docker Desktop, run "
            "`docker compose up --build` in d36, and wait for redis to become healthy."
        )
    if "could not translate host name" in detail or "name or service not known" in detail:
        return (
            "Redis host not found. Inside Docker Compose use host `redis`. "
            "On your laptop outside Docker use `localhost` in REDIS_URL."
        )
    return (
        "Redis is not ready yet. Wait ~20 seconds after `docker compose up`, then run "
        "`docker compose logs redis` and look for 'Ready to accept connections'."
    )


class RedisClient:
    def __init__(self, url: str = REDIS_URL) -> None:
        self.client = redis.from_url(url, decode_responses=True)

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def status(self) -> tuple[bool, str]:
        try:
            if self.client.ping():
                return True, "Redis connection OK"
        except Exception as exc:
            return False, redis_connection_hint(exc)
        return False, redis_connection_hint()


_redis_client: Optional[RedisClient] = None


def get_redis() -> RedisClient:
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
    return _redis_client
