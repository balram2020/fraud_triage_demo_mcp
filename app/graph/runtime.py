"""Graph invocation wrapper — keeps routes thin."""

from __future__ import annotations

from typing import Any

from app.graph.builder import COST_PER_SKILL, DEFAULT_MODEL, build_graph


class GraphRuntime:
    def __init__(self) -> None:
        self.graph = build_graph()

    def invoke(self, message: str, trace_id: str = "test", session_id: str = "test") -> dict[str, Any]:
        initial_state: dict[str, Any] = {
            "message": message,
            "user_request": message,
            "trace_id": trace_id,
            "session_id": session_id,
            "blocked": False,
            "block_reason": "",
            "skill_plan": {},
            "validation_errors": [],
            "final_answer": {},
            "total_cost_usd": 0.0,
            "skills": [],
            "skill_outputs": [],
            "answer": "",
            "escalated": False,
            "trace_events": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_cost_usd": 0.0,
            "used_mock_llm": False,
            "audit_trail": [],
            "handoffs": [],
        }
        result = self.graph.invoke(initial_state)
        import json as _json
        print("DEBUG GRAPH RESULT KEYS:", list(result.keys()), flush=True)
        print("DEBUG final_answer:", _json.dumps(result.get("final_answer", {}), default=str)[:500], flush=True)
        print("DEBUG answer:", repr(result.get("answer", "")), flush=True)
        final_answer = result.get("final_answer") or {}
        answer_text = final_answer.get("answer", "") or result.get("answer", "")
        skills = final_answer.get("skills_used", result.get("skills", ["rag_skill"]))
        skill_cost = len(skills) * COST_PER_SKILL
        llm_cost = float(result.get("llm_cost_usd", 0.0))
        return {
            "answer": answer_text,
            "skills_used": skills,
            "escalated": bool(result.get("escalated")),
            "trace_events": result.get("trace_events", []),
            "total_cost_usd": round(skill_cost + llm_cost, 6),
            "model_name": DEFAULT_MODEL,
            "input_tokens": int(result.get("input_tokens", 0)),
            "output_tokens": int(result.get("output_tokens", 0)),
            "used_mock_llm": bool(result.get("used_mock_llm", False)),
            "audit_trail": result.get("audit_trail", []),
            "handoffs": result.get("handoffs", []),
        }
