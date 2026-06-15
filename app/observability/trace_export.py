"""Structured JSON trace export for each completed graph run (deployment-friendly).

Uses ``json.dump`` to ``HARDEN_TRACE_DIR`` (default: ``<project_root>/traces``).
Disable with ``HARDEN_TRACE_EXPORT=0``.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import project_root

SCHEMA_VERSION = "1.0"

# Serializable slice of graph state (matches AgentState from builder.py).
_TRACE_KEYS: tuple[str, ...] = (
    "trace_id",
    "session_id",
    "user_request",
    "blocked",
    "block_reason",
    "skill_plan",
    "validation_errors",
    "final_answer",
    "total_cost_usd",
    "skills",
    "skill_outputs",
    "answer",
    "escalated",
    "trace_events",
    "input_tokens",
    "output_tokens",
    "llm_cost_usd",
    "used_mock_llm",
)


def trace_export_dir() -> Path:
    raw = os.getenv("HARDEN_TRACE_DIR", "").strip()
    return Path(raw) if raw else (project_root() / "traces")


def trace_export_enabled() -> bool:
    return os.getenv("HARDEN_TRACE_EXPORT", "1").strip().lower() not in ("0", "false", "no", "off")


def _pretty_json() -> bool:
    return os.getenv("HARDEN_TRACE_JSON_PRETTY", "").strip().lower() in ("1", "true", "yes", "on")


def _safe_filename(trace_id: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", trace_id.strip())[:200]
    return s or "unknown_trace"


def build_trace_record(state: dict[str, Any]) -> dict[str, Any]:
    record = {k: state.get(k) for k in _TRACE_KEYS}
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "record": record,
    }


def export_graph_trace_json(state: dict[str, Any]) -> tuple[Path | None, str | None]:
    """
    Writes one JSON file per graph completion. Returns ``(path, error)``;
    on failure ``path`` is None and ``error`` is a short message (graph still succeeds).
    """
    if not trace_export_enabled():
        return None, None

    trace_id = str(state.get("trace_id") or "unknown")
    out_dir = trace_export_dir()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return None, f"trace_dir_mkdir:{e}"

    path = out_dir / f"{_safe_filename(trace_id)}.json"
    payload = build_trace_record(state)

    kwargs: dict[str, Any] = {}
    if _pretty_json():
        kwargs["indent"] = 2
    else:
        kwargs["separators"] = (",", ":")

    try:
        with path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, **kwargs)
    except OSError as e:
        return None, f"trace_write:{e}"
    except (TypeError, ValueError) as e:
        return None, f"trace_serialize:{e}"

    return path, None
