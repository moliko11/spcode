from __future__ import annotations

import asyncio

from packages.runtime.bootstrap import build_runtime
from packages.runtime.models import AgentState, ToolCall


def _call(tool_name: str, arguments: dict) -> ToolCall:
    return ToolCall(
        call_id=f"call-{tool_name}",
        tool_name=tool_name,
        arguments=arguments,
        idempotency_key="",
    )


def test_plan_mode_blocks_side_effect_tools(tmp_path) -> None:
    runtime = build_runtime(workspace_root=tmp_path)
    state = AgentState(run_id="run-plan-mode", user_id="u1", session_id="s1", task="plan")

    entered = asyncio.run(
        runtime.tool_executor.execute(
            state,
            _call("enter_plan_mode", {"goal": "ship safely", "reason": "plan only"}),
        )
    )
    assert entered.ok is True
    assert state.metadata["plan_mode"]["active"] is True

    blocked = asyncio.run(
        runtime.tool_executor.execute(
            state,
            _call("file_write", {"path": "blocked.txt", "content": "nope", "mode": "create"}),
        )
    )
    assert blocked.ok is False
    assert "plan mode blocks" in (blocked.error or "")
    assert not (tmp_path / "blocked.txt").exists()

    exited = asyncio.run(
        runtime.tool_executor.execute(
            state,
            _call("exit_plan_mode", {"decision": "approved"}),
        )
    )
    assert exited.ok is True
    assert state.metadata["plan_mode"]["active"] is False


def test_runtime_registers_plan_mode_tools(tmp_path) -> None:
    runtime = build_runtime(workspace_root=tmp_path)

    assert runtime.registry.get_spec("enter_plan_mode").name == "enter_plan_mode"
    assert runtime.registry.get_spec("exit_plan_mode").name == "exit_plan_mode"
    assert runtime.registry.get_spec("todo_write").name == "todo_write"
