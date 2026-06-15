"""
Idempotency keys — retries should not create duplicate work.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.redis_controls import IDEMPOTENCY_TTL_SEC, get_redis


def check_idempotency(key: str) -> Optional[dict[str, Any]]:
    raw = get_redis().client.get(f"idempotency:{key}")
    if raw:
        return json.loads(raw)
    return None


def store_idempotency_result(key: str, response: dict[str, Any]) -> None:
    get_redis().client.set(
        f"idempotency:{key}",
        json.dumps(response),
        ex=IDEMPOTENCY_TTL_SEC,
    )
