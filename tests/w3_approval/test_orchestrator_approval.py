from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from packages.orchestrator.models import StepRunStatus
from packages.orchestrator.orchestrator import Orchestrator
from packages.orchestrator.store import PlanRunStore
from packages.planner.planner import Planner
from packages.planner.store import PlanStore
from packages.runtime.models import AgentState, RunStatus


def _mock_llm_with_plan(steps_data: list[dict]) -> MagicMock:
    content = json.dumps({"goal": "测试目标", "steps": steps_data})
    resp = MagicMock()
    resp.content = content
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


def _agent_state(
    status: RunStatus = RunStatus.COMPLETED,
    run_id: str = "run-x",
    output: str = "ok",
    pending: dict | None = None,
) -> MagicMock:
    state = MagicMock(spec=AgentState)
    state.run_id = run_id
    state.status = status
    state.final_output = output
    state.failure_reason = None if status != RunStatus.FAILED else "error"
    state.pending_human_request = pending
    return state


def _three_step_llm() -> MagicMock:
    return _mock_llm_with_plan([
        {"step_id": "step_1", "title": "步骤1", "description": "d1", "dependencies": [], "acceptance_criteria": ["c1"], "suggested_tools": []},
        {"step_id": "step_2", "title": "步骤2", "description": "d2", "dependencies": ["step_1"], "acceptance_criteria": ["c2"], "suggested_tools": ["FileWriteTool"]},
        {"step_id": "step_3", "title": "步骤3", "description": "d3", "dependencies": ["step_2"], "acceptance_criteria": ["c3"], "suggested_tools": []},
    ])


@pytest.mark.asyncio
async def test_orchestrator_pauses_on_waiting_human(tmp_path):
    runtime = MagicMock()
    runtime.chat = AsyncMock(side_effect=[
        _agent_state(RunStatus.COMPLETED, run_id="run-1", output="done 1"),
        _agent_state(
            RunStatus.WAITING_HUMAN,
            run_id="run-2",
            pending={"context": {"tool_name": "file_write"}},
        ),
    ])
    orch = Orchestrator(
        runtime=runtime,
        planner=Planner(llm=_three_step_llm()),
        plan_store=PlanStore(tmp_path / "plans"),
        plan_run_store=PlanRunStore(tmp_path / "plan_runs"),
    )

    plan_run = await orch.run("测试目标")

    assert plan_run.status == "waiting_human"
    assert plan_run.pending_step_id == "step_2"
    assert plan_run.step_runs[1].status == StepRunStatus.WAITING_HUMAN


@pytest.mark.asyncio
async def test_orchestrator_resume_after_approval_continues_remaining_steps(tmp_path):
    runtime = MagicMock()
    runtime.chat = AsyncMock(side_effect=[
        _agent_state(RunStatus.COMPLETED, run_id="run-1", output="done 1"),
        _agent_state(RunStatus.WAITING_HUMAN, run_id="run-2", pending={"context": {"tool_name": "file_write"}}),
        _agent_state(RunStatus.COMPLETED, run_id="run-3", output="done 3"),
    ])
    runtime.resume = AsyncMock(return_value=_agent_state(RunStatus.COMPLETED, run_id="run-2", output="done 2"))
    orch = Orchestrator(
        runtime=runtime,
        planner=Planner(llm=_three_step_llm()),
        plan_store=PlanStore(tmp_path / "plans"),
        plan_run_store=PlanRunStore(tmp_path / "plan_runs"),
    )

    paused = await orch.run("测试目标")
    resumed = await orch.resume(paused.plan_run_id, approved=True, approved_by="tester")

    assert resumed.status == "completed"
    assert resumed.completed is True
    assert [item.status for item in resumed.step_runs] == [
        StepRunStatus.COMPLETED,
        StepRunStatus.COMPLETED,
        StepRunStatus.COMPLETED,
    ]
    assert resumed.step_runs[1].output == "done 2"
    assert resumed.step_runs[2].output == "done 3"


@pytest.mark.asyncio
async def test_orchestrator_resume_rejected_marks_failed(tmp_path):
    runtime = MagicMock()
    runtime.chat = AsyncMock(side_effect=[
        _agent_state(RunStatus.COMPLETED, run_id="run-1", output="done 1"),
        _agent_state(RunStatus.WAITING_HUMAN, run_id="run-2", pending={"context": {"tool_name": "file_write"}}),
    ])
    runtime.resume = AsyncMock(
        return_value=_agent_state(RunStatus.COMPLETED, run_id="run-2", output="human approval rejected; execution stopped")
    )
    orch = Orchestrator(
        runtime=runtime,
        planner=Planner(llm=_three_step_llm()),
        plan_store=PlanStore(tmp_path / "plans"),
        plan_run_store=PlanRunStore(tmp_path / "plan_runs"),
    )

    paused = await orch.run("测试目标")
    resumed = await orch.resume(paused.plan_run_id, approved=False)

    assert resumed.status == "failed"
    assert resumed.step_runs[1].status == StepRunStatus.FAILED
    assert "rejected" in (resumed.step_runs[1].error or "")
