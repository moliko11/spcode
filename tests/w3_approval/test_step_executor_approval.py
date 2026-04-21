from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from packages.orchestrator.executor import StepExecutor
from packages.orchestrator.models import StepRun, StepRunStatus
from packages.planner.models import TaskStep
from packages.runtime.models import AgentState, RunStatus


def _make_state(
    status: RunStatus,
    run_id: str = "run-1",
    output: str | None = None,
    failure: str | None = None,
    pending: dict | None = None,
) -> MagicMock:
    state = MagicMock(spec=AgentState)
    state.run_id = run_id
    state.status = status
    state.final_output = output
    state.failure_reason = failure
    state.pending_human_request = pending
    return state


def _make_step() -> TaskStep:
    return TaskStep(step_id="step_1", title="创建文件", description="创建 fibonacci.py")


@pytest.mark.asyncio
async def test_step_executor_waiting_human():
    runtime = MagicMock()
    runtime.chat = AsyncMock(
        return_value=_make_state(
            RunStatus.WAITING_HUMAN,
            pending={"context": {"tool_name": "file_write"}},
        )
    )
    executor = StepExecutor(runtime=runtime, user_id="u", session_id="s")
    step_run = StepRun(step_id="step_1", title="创建文件")

    result = await executor.run(_make_step(), step_run)

    assert result.status == StepRunStatus.WAITING_HUMAN
    assert result.pending_human_request["context"]["tool_name"] == "file_write"


@pytest.mark.asyncio
async def test_step_executor_resume_approved():
    runtime = MagicMock()
    runtime.resume = AsyncMock(return_value=_make_state(RunStatus.COMPLETED, output="created"))
    executor = StepExecutor(runtime=runtime, user_id="u", session_id="s")
    step_run = StepRun(
        step_id="step_1",
        title="创建文件",
        status=StepRunStatus.WAITING_HUMAN,
        run_id="run-1",
        pending_human_request={"context": {"tool_name": "file_write"}},
    )

    result = await executor.resume(step_run, approved=True, approved_by="tester")

    assert result.status == StepRunStatus.COMPLETED
    assert result.output == "created"
    assert result.pending_human_request is None


@pytest.mark.asyncio
async def test_step_executor_resume_rejected():
    runtime = MagicMock()
    runtime.resume = AsyncMock(
        return_value=_make_state(RunStatus.COMPLETED, output="human approval rejected; execution stopped")
    )
    executor = StepExecutor(runtime=runtime, user_id="u", session_id="s")
    step_run = StepRun(step_id="step_1", title="创建文件", run_id="run-1")

    result = await executor.resume(step_run, approved=False)

    assert result.status == StepRunStatus.FAILED
    assert "rejected" in (result.error or "")
