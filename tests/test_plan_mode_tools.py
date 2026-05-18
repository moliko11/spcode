from __future__ import annotations

import asyncio

from packages.tools import EnterPlanModeTool, ExitPlanModeTool
from packages.runtime.guardrail import GuardrailEngine, GuardrailViolation


def test_enter_exit_plan_mode_tools_return_state() -> None:
    enter = EnterPlanModeTool()
    entered = asyncio.run(enter.arun({"goal": "ship m2", "reason": "plan first", "constraints": ["no writes"]}))

    assert entered["plan_mode"]["active"] is True
    assert entered["plan_mode"]["goal"] == "ship m2"

    exit_tool = ExitPlanModeTool()
    exited = asyncio.run(
        exit_tool.arun(
            {
                "plan_mode_session_id": entered["plan_mode"]["plan_mode_session_id"],
                "decision": "approved",
            }
        )
    )

    assert exited["plan_mode"]["active"] is False
    assert exited["plan_mode"]["decision"] == "approved"


def test_guardrail_validates_plan_mode_args() -> None:
    guardrail = GuardrailEngine()

    guardrail.validate_tool_args("enter_plan_mode", {"goal": "g", "reason": "r"})
    guardrail.validate_tool_args("exit_plan_mode", {"decision": "revise_required"})

    try:
        guardrail.validate_tool_args("enter_plan_mode", {"goal": "", "reason": "r"})
    except GuardrailViolation as exc:
        assert "goal" in str(exc)
    else:
        raise AssertionError("expected invalid goal to fail")

    try:
        guardrail.validate_tool_args("exit_plan_mode", {"decision": "later"})
    except GuardrailViolation as exc:
        assert "decision" in str(exc)
    else:
        raise AssertionError("expected invalid decision to fail")
