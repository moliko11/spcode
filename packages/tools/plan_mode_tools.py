from __future__ import annotations

import time
import uuid
from typing import Any


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


class EnterPlanModeTool:
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        goal = str(arguments.get("goal") or "").strip()
        reason = str(arguments.get("reason") or "").strip()
        if not goal:
            raise ValueError("goal is required")
        if not reason:
            raise ValueError("reason is required")
        plan_mode_session_id = str(arguments.get("plan_mode_session_id") or f"planmode_{uuid.uuid4().hex[:10]}")
        plan_mode = {
            "active": True,
            "plan_mode_session_id": plan_mode_session_id,
            "goal": goal,
            "reason": reason,
            "constraints": _as_list(arguments.get("constraints")),
            "entered_at": time.time(),
        }
        return {
            "ok": True,
            "tool_name": "enter_plan_mode",
            "plan_mode": plan_mode,
            "output": "plan mode entered; side-effect tools are blocked until exit_plan_mode",
            "changed_files": [],
            "metadata": {"plan_mode": plan_mode},
        }


class ExitPlanModeTool:
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        decision = str(arguments.get("decision") or "").strip().lower()
        if decision not in {"approved", "rejected", "revise_required"}:
            raise ValueError("decision must be approved, rejected, or revise_required")
        plan_mode = {
            "active": False,
            "plan_mode_session_id": str(arguments.get("plan_mode_session_id") or ""),
            "plan_id": str(arguments.get("plan_id") or ""),
            "decision": decision,
            "notes": str(arguments.get("notes") or ""),
            "exited_at": time.time(),
        }
        return {
            "ok": True,
            "tool_name": "exit_plan_mode",
            "plan_mode": plan_mode,
            "output": f"plan mode exited: {decision}",
            "changed_files": [],
            "metadata": {"plan_mode": plan_mode},
        }
