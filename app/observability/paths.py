"""Resolve project-root path for trace export and other file-based observability."""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """Return the top-level project directory (fraud_triage_demo/)."""
    # observability/paths.py  →  observability/  →  app/  →  fraud_triage_demo/
    return Path(__file__).resolve().parent.parent.parent
