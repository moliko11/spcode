"""W2 阶段 — StepExecutor 测试"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from packages.orchestrator.executor import StepExecutor
from packages.orchestrator.models import StepRun, StepRunStatus
from packages.planner.models import TaskStep
from packages.runtime.models import AgentState, RunStatus


def _make_runtime(status: RunStatus = RunStatus.COMPLETED, output: str = "done", failure: str | None = None) -> MagicMock:
    state = MagicMock(spec=AgentState)
    state.run_id = "run-test"
    state.status = status
    state.final_output = output
    state.failure_reason = failure
    runtime = MagicMock()
    runtime.chat = AsyncMock(return_value=state)
    return runtime


def _make_step(**kwargs) -> TaskStep:
    defaults = dict(step_id="s1", title="测试步骤", description="描述")
    defaults.update(kwargs)
    return TaskStep(**defaults)


class TestStepExecutor:
    @pytest.mark.asyncio
    async def test_success(self):
        runtime = _make_runtime(RunStatus.COMPLETED, output="result")
        executor = StepExecutor(runtime=runtime, user_id="u", session_id="sess")
        step = _make_step()
        step_run = StepRun(step_id=step.step_id, title=step.title)

        result = await executor.run(step, step_run)

        assert result.status == StepRunStatus.COMPLETED
        assert result.output == "result"
        assert result.run_id == "run-test"
        assert result.started_at is not None
        assert result.finished_at is not None
        assert result.error is None

    @pytest.mark.asyncio
    async def test_failure_status(self):
        runtime = _make_runtime(RunStatus.FAILED, failure="timeout")
        executor = StepExecutor(runtime=runtime, user_id="u", session_id="sess")
        step = _make_step()
        step_run = StepRun(step_id=step.step_id, title=step.title)

        result = await executor.run(step, step_run)

        assert result.status == StepRunStatus.FAILED
        assert "timeout" in (result.error or "")

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        runtime = MagicMock()
        runtime.chat = AsyncMock(side_effect=RuntimeError("LLM crashed"))
        executor = StepExecutor(runtime=runtime, user_id="u", session_id="sess")
        step = _make_step()
        step_run = StepRun(step_id=step.step_id, title=step.title)

        result = await executor.run(step, step_run)

        assert result.status == StepRunStatus.FAILED
        assert "LLM crashed" in (result.error or "")
        # 即使异常，时间戳也应被记录
        assert result.finished_at is not None

    @pytest.mark.asyncio
    async def test_prompt_contains_criteria(self):
        """prompt 应包含验收标准"""
        runtime = _make_runtime()
        executor = StepExecutor(runtime=runtime, user_id="u", session_id="sess")
        step = _make_step(acceptance_criteria=["文件必须存在", "输出 hello"])
        step_run = StepRun(step_id=step.step_id, title=step.title)
        await executor.run(step, step_run)

        prompt = runtime.chat.call_args.kwargs["message"]
        assert "文件必须存在" in prompt
        assert "输出 hello" in prompt
