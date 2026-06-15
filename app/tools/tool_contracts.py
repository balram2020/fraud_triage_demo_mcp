"""Structured success and error envelopes returned by every MCP tool.

Every tool MUST return one of these two shapes so the LLM can reliably
interpret the result without guessing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def success_response(
    data: Dict[str, Any],
    source: str,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
) -> dict:
    return {
        "success": True,
        "data": data,
        "metadata": {
            "source": source,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
        },
    }


def error_response(
    error_type: str,
    message: str,
    recoverable: bool = True,
    retry_after_seconds: Optional[int] = None,
    fallback_suggestion: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> dict:
    return {
        "success": False,
        "error_type": error_type,
        "message": message,
        "recoverable": recoverable,
        "retry_after_seconds": retry_after_seconds,
        "fallback_suggestion": fallback_suggestion,
        "details": details or {},
    }
