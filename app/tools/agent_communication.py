"""
Agent Communication Patterns (production-integrated)
-----------------------------------------------------
Provides four enterprise patterns used throughout the fraud-triage graph:

  1. **State handoff** — structured ``AgentHandoff`` passed between graph nodes
     so each node knows *who* sent the work, *why*, and with what context.
  2. **Structured message passing** — typed ``AgentMessage`` objects for
     inter-agent communication with sender / receiver / payload.
  3. **Agent-as-tool pattern** — ``invoke_agent_as_tool`` lets any node call
     another agent (skill) as if it were a tool, with contract enforcement.
  4. **Audit trail** — ``AuditLog`` records every handoff, message, and
     agent-as-tool call with timestamps so you can trace *who said what*.

All classes are pure-Python dataclasses (no LLM dependency) so they work
identically in mock and live modes.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ─────────────────────────────────────────────────────────────
# 1. State Handoff — structured context passed between nodes
# ─────────────────────────────────────────────────────────────

@dataclass
class AgentHandoff:
    """Structured context passed from one graph node to another.

    Every handoff carries *who* originated the request, *who* should handle it,
    the user's task, and an arbitrary context dict extracted by the sender.
    """

    from_agent: str
    to_agent: str
    task: str
    context: dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"           # low | normal | high
    handoff_id: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.handoff_id:
            self.handoff_id = f"hoff_{uuid.uuid4().hex[:12]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_prompt_context(self) -> str:
        """Render a human-readable block suitable for injection into an LLM prompt."""
        return (
            f"HANDOFF FROM {self.from_agent.upper()} → {self.to_agent.upper()}:\n"
            f"  Handoff ID : {self.handoff_id}\n"
            f"  Task       : {self.task}\n"
            f"  Priority   : {self.priority}\n"
            f"  Context    : {json.dumps(self.context, indent=2)}\n"
            f"  Received at: {self.timestamp}"
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────
# 2. Structured Message Passing — typed inter-agent messages
# ─────────────────────────────────────────────────────────────

@dataclass
class AgentMessage:
    """A single typed message exchanged between agents.

    Messages can carry structured payloads (not just text) so downstream
    agents can programmatically inspect what the upstream agent produced.
    """

    sender: str
    receiver: str
    message_type: str                   # e.g. "skill_result", "escalation_request", "handoff"
    payload: dict[str, Any] = field(default_factory=dict)
    text: str = ""                      # optional human-readable summary
    message_id: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.message_id:
            self.message_id = f"msg_{uuid.uuid4().hex[:12]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────
# 3. Agent-as-Tool — call any registered skill as a "tool"
# ─────────────────────────────────────────────────────────────

# Registry populated at import time from simulated_tools; production systems
# would load this from a service catalogue.
_AGENT_TOOL_REGISTRY: dict[str, Any] = {}


def register_agent_tool(name: str, fn: Any) -> None:
    """Register a callable as an agent-tool so other agents can invoke it."""
    _AGENT_TOOL_REGISTRY[name] = fn


def list_agent_tools() -> list[str]:
    """Return the names of all registered agent-tools."""
    return list(_AGENT_TOOL_REGISTRY.keys())


def invoke_agent_as_tool(
    caller: str,
    tool_name: str,
    args: dict[str, Any],
    audit_log: AuditLog | None = None,
) -> dict[str, Any]:
    """Invoke a registered agent-tool by name, with audit logging.

    Returns a dict ``{"ok": bool, "result": Any, "error": str | None}``.
    """
    t0 = time.time()
    entry = AuditEntry(
        actor=caller,
        action="agent_as_tool_invoke",
        target=tool_name,
        detail={"args": args},
    )

    fn = _AGENT_TOOL_REGISTRY.get(tool_name)
    if fn is None:
        entry.detail["error"] = f"tool '{tool_name}' not found in registry"
        if audit_log is not None:
            audit_log.append(entry)
        return {"ok": False, "result": None, "error": entry.detail["error"]}

    try:
        result = fn(**args)
        latency_ms = int((time.time() - t0) * 1000)
        entry.detail.update({"ok": True, "latency_ms": latency_ms})
        if audit_log is not None:
            audit_log.append(entry)
        return {"ok": True, "result": result, "error": None}
    except Exception as exc:
        latency_ms = int((time.time() - t0) * 1000)
        entry.detail.update({"ok": False, "latency_ms": latency_ms, "error": str(exc)})
        if audit_log is not None:
            audit_log.append(entry)
        return {"ok": False, "result": None, "error": str(exc)}


# ─────────────────────────────────────────────────────────────
# 4. Audit Trail — "who said what" across the graph run
# ─────────────────────────────────────────────────────────────

@dataclass
class AuditEntry:
    """One row in the audit trail."""

    actor: str                          # which agent / node
    action: str                         # e.g. "handoff_created", "message_sent", "agent_as_tool_invoke"
    target: str = ""                    # who / what was acted upon
    detail: dict[str, Any] = field(default_factory=dict)
    entry_id: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.entry_id:
            self.entry_id = f"aud_{uuid.uuid4().hex[:12]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditLog:
    """Append-only audit log for a single graph run.

    Collects ``AuditEntry`` objects and can be serialised to a list of dicts
    for inclusion in trace export JSON.
    """

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def append(self, entry: AuditEntry) -> None:
        self._entries.append(entry)

    def record_handoff(self, handoff: AgentHandoff) -> None:
        """Convenience: record a handoff as an audit entry."""
        self.append(
            AuditEntry(
                actor=handoff.from_agent,
                action="handoff_created",
                target=handoff.to_agent,
                detail=handoff.to_dict(),
            )
        )

    def record_message(self, message: AgentMessage) -> None:
        """Convenience: record a structured message as an audit entry."""
        self.append(
            AuditEntry(
                actor=message.sender,
                action="message_sent",
                target=message.receiver,
                detail=message.to_dict(),
            )
        )

    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries]

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"AuditLog({len(self._entries)} entries)"


# ─────────────────────────────────────────────────────────────
# Helper: create a handoff + audit in one call (used by nodes)
# ─────────────────────────────────────────────────────────────

def create_handoff(
    from_agent: str,
    to_agent: str,
    task: str,
    context: dict[str, Any] | None = None,
    priority: str = "normal",
    audit_log: AuditLog | None = None,
) -> AgentHandoff:
    """Build an ``AgentHandoff`` and optionally record it in the audit log."""
    handoff = AgentHandoff(
        from_agent=from_agent,
        to_agent=to_agent,
        task=task,
        context=context or {},
        priority=priority,
    )
    if audit_log is not None:
        audit_log.record_handoff(handoff)
    return handoff


def send_message(
    sender: str,
    receiver: str,
    message_type: str,
    payload: dict[str, Any] | None = None,
    text: str = "",
    audit_log: AuditLog | None = None,
) -> AgentMessage:
    """Build an ``AgentMessage`` and optionally record it in the audit log."""
    msg = AgentMessage(
        sender=sender,
        receiver=receiver,
        message_type=message_type,
        payload=payload or {},
        text=text,
    )
    if audit_log is not None:
        audit_log.record_message(msg)
    return msg
