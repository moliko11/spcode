from __future__ import annotations

import pytest

from packages.runtime.budget import BudgetController
from packages.runtime.models import AgentState, BudgetExceeded, ToolResult


def _result(tool_name: str, **metadata: str) -> ToolResult:
    return ToolResult(call_id=tool_name, tool_name=tool_name, ok=True, metadata=dict(metadata))


def _state(results: list[ToolResult]) -> AgentState:
    return AgentState(run_id="r1", user_id="u1", task="t", session_id="s1", tool_results=results)


def test_task_tools_do_not_consume_main_tool_budget() -> None:
    state = _state(
        [
            _result("task_create", budget_category="state", tool_category="workflow"),
            _result("task_update", budget_category="state", tool_category="workflow"),
            _result("task_list", budget_category="state", tool_category="workflow"),
        ]
    )
    budget = BudgetController(max_steps=10, max_tool_calls=1, max_seconds=30, max_state_tool_calls=10)

    assert budget.count_tool_calls(state)["main_tool_calls"] == 0
    assert budget.count_tool_calls(state)["state_tool_calls"] == 3
    budget.check(state)


def test_read_tools_use_read_budget_instead_of_main_budget() -> None:
    state = _state([_result("file_read"), _result("glob"), _result("grep"), _result("list_dir")])
    budget = BudgetController(max_steps=10, max_tool_calls=1, max_seconds=30, max_read_tool_calls=10)

    counts = budget.count_tool_calls(state)
    assert counts["main_tool_calls"] == 0
    assert counts["read_tool_calls"] == 4
    budget.check(state)


def test_main_budget_still_applies_to_uncategorized_tools() -> None:
    state = _state([_result("calculator")])
    budget = BudgetController(max_steps=10, max_tool_calls=1, max_seconds=30)

    with pytest.raises(BudgetExceeded, match="max tool calls exceeded"):
        budget.check(state)


def test_task_stop_counts_as_state_and_high_risk() -> None:
    state = _state(
        [
            _result(
                "task_stop",
                budget_category="state",
                tool_category="workflow",
                risk_level="medium",
                side_effect="local_fs",
            )
        ]
    )
    budget = BudgetController(max_steps=10, max_tool_calls=1, max_seconds=30, max_high_risk_tool_calls=1)

    counts = budget.count_tool_calls(state)
    assert counts["state_tool_calls"] == 1
    assert counts["main_tool_calls"] == 0
    assert counts["high_risk_tool_calls"] == 1
    with pytest.raises(BudgetExceeded, match="max high risk tool calls exceeded"):
        budget.check(state)
