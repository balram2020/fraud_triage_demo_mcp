"""User and tenant request counters — runtime throttle layer."""

from __future__ import annotations

from app.redis_controls import RATE_LIMIT_MAX, RATE_LIMIT_WINDOW_SEC, get_redis


def check_rate_limit(tenant_id: str, user_id: str) -> tuple[bool, str]:
    store = get_redis().client
    key = f"rate:{tenant_id}:{user_id}"
    count = store.incr(key)
    if count == 1:
        store.expire(key, RATE_LIMIT_WINDOW_SEC)
    if count > RATE_LIMIT_MAX:
        return False, f"Rate limit exceeded ({count}/{RATE_LIMIT_MAX})"
    return True, f"Allowed ({count}/{RATE_LIMIT_MAX})"
