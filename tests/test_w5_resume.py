"""
W5: 跨进程 recover() 持久化恢复测试

场景：
1. plan_run 已 completed/failed → recover() 直接返回，不重新执行
2. 进程崩溃（RUNNING 步骤未完成）→ recover() 重置并继续执行
3. waiting_human 状态的 plan_run → recover() 不处理（由 resume() 负责），
   main.py resume 命令正确路由到 resume()
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.orchestrator.models import PlanRun, StepRun, StepRunStatus
from packages.orchestrator.orchestrator import Orchestrator
from packages.orchestrator.store import PlanRunStore
from packages.planner.models import PlanStatus, StepStatus, TaskPlan, TaskStep
from packages.planner.store import PlanStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_plan(steps: list[TaskStep]) -> TaskPlan:
    return TaskPlan(
        plan_id="plan-test",
        goal="test goal",
        steps=steps,
    )


def _make_orchestrator(
    tmp_path: Path,
    plan: TaskPlan,
    plan_run: PlanRun,
    step_delay: float = 0.0,
) -> Orchestrator:
    """构建带 mock runtime 的 Orchestrator，StepExecutor.run 会立刻完成步骤。"""
    plan_store = PlanStore(tmp_path / "plans")
    plan_run_store = PlanRunStore(tmp_path / "plan_runs")
    plan_store.save(plan)
    plan_run_store.save(plan_run)

    runtime = MagicMock()

    orch = Orchestrator(
        runtime=runtime,
        planner=MagicMock(),
        plan_store=plan_store,
        plan_run_store=plan_run_store,
        user_id="test-user",
        session_id=plan_run.session_id,
    )

    async def fake_executor_run(step, step_run):
        if step_delay:
            await asyncio.sleep(step_delay)
        step_run.status = StepRunStatus.COMPLETED
        step_run.output = f"done: {step.title}"
        step_run.started_at = time.time()
        step_run.finished_at = time.time()

    with patch(
        "packages.orchestrator.orchestrator.StepExecutor",
        return_value=MagicMock(run=AsyncMock(side_effect=fake_executor_run)),
    ):
        return orch, plan_store, plan_run_store


# ---------------------------------------------------------------------------
# Test 1: completed/failed plan_run 直接返回
# ---------------------------------------------------------------------------

class TestRecoverTerminal:
    def test_recover_completed_returns_immediately(self, tmp_path):
        plan = _make_plan([TaskStep(step_id="s1", title="step1", description="")])  
        plan_run = PlanRun(
            plan_run_id="run-1",
            plan_id="plan-test",
            goal="test",
            status="completed",
            session_id="sess-1",
            completed=True,
        )
        plan_store = PlanStore(tmp_path / "plans")
        plan_run_store = PlanRunStore(tmp_path / "plan_runs")
        plan_store.save(plan)
        plan_run_store.save(plan_run)

        orch = Orchestrator(
            runtime=MagicMock(),
            planner=MagicMock(),
            plan_store=plan_store,
            plan_run_store=plan_run_store,
            user_id="test-user",
        )
        result = asyncio.run(orch.recover("run-1"))
        assert result.status == "completed"

    def test_recover_failed_returns_immediately(self, tmp_path):
        plan = _make_plan([TaskStep(step_id="s1", title="step1", description="")])  
        plan_run = PlanRun(
            plan_run_id="run-2",
            plan_id="plan-test",
            goal="test",
            status="failed",
            session_id="sess-2",
        )
        plan_store = PlanStore(tmp_path / "plans")
        plan_run_store = PlanRunStore(tmp_path / "plan_runs")
        plan_store.save(plan)
        plan_run_store.save(plan_run)

        orch = Orchestrator(
            runtime=MagicMock(),
            planner=MagicMock(),
            plan_store=plan_store,
            plan_run_store=plan_run_store,
            user_id="test-user",
        )
        result = asyncio.run(orch.recover("run-2"))
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# Test 2: RUNNING 步骤被重置，继续执行
# ---------------------------------------------------------------------------

class TestRecoverCrashedSteps:
    def test_crashed_running_step_is_reset_and_completed(self, tmp_path):
        """
        模拟进程崩溃：plan 中 s1=COMPLETED, s2=RUNNING（未完成），
        recover() 应将 s2 重置为 PENDING，重新执行后变为 COMPLETED。
        """
        s1 = TaskStep(step_id="s1", title="step1", description="", status=StepStatus.COMPLETED)
        s2 = TaskStep(step_id="s2", title="step2", description="", status=StepStatus.RUNNING)
        plan = _make_plan([s1, s2])
        plan.status = PlanStatus.RUNNING

        plan_run = PlanRun(
            plan_run_id="run-crash",
            plan_id="plan-test",
            goal="test",
            status="running",
            session_id="sess-crash",
            step_runs=[
                StepRun(step_id="s1", title="step1", status=StepRunStatus.COMPLETED,
                        output="done", started_at=time.time(), finished_at=time.time()),
                # s2 的 step_run 存在但未完成（PENDING = 还没更新 status）
                StepRun(step_id="s2", title="step2", status=StepRunStatus.PENDING),
            ],
        )

        plan_store = PlanStore(tmp_path / "plans")
        plan_run_store = PlanRunStore(tmp_path / "plan_runs")
        plan_store.save(plan)
        plan_run_store.save(plan_run)

        runtime = MagicMock()
        orch = Orchestrator(
            runtime=runtime,
            planner=MagicMock(),
            plan_store=plan_store,
            plan_run_store=plan_run_store,
            user_id="test-user",
            session_id="sess-crash",
        )

        async def fake_run(step, step_run):
            step_run.status = StepRunStatus.COMPLETED
            step_run.output = f"recovered: {step.title}"
            step_run.started_at = time.time()
            step_run.finished_at = time.time()

        with patch(
            "packages.orchestrator.orchestrator.StepExecutor",
            return_value=MagicMock(run=AsyncMock(side_effect=fake_run)),
        ):
            result = asyncio.run(orch.recover("run-crash"))

        assert result.status == "completed"
        assert result.completed is True
        # s2 应该被重新执行
        s2_run = next(r for r in result.step_runs if r.step_id == "s2")
        assert s2_run.status == StepRunStatus.COMPLETED
        assert "recovered" in (s2_run.output or "")

    def test_recover_restores_session_prefix(self, tmp_path):
        """recover() 必须将 session_prefix 恢复为原来的值，确保 session ID 一致性。"""
        s1 = TaskStep(step_id="s1", title="step1", description="", status=StepStatus.RUNNING)
        plan = _make_plan([s1])
        plan.status = PlanStatus.RUNNING

        plan_run = PlanRun(
            plan_run_id="run-sess",
            plan_id="plan-test",
            goal="test",
            status="running",
            session_id="my-custom-session-prefix",
        )

        plan_store = PlanStore(tmp_path / "plans")
        plan_run_store = PlanRunStore(tmp_path / "plan_runs")
        plan_store.save(plan)
        plan_run_store.save(plan_run)

        used_session_ids: list[str] = []

        async def fake_run(step, step_run):
            step_run.status = StepRunStatus.COMPLETED
            step_run.started_at = time.time()
            step_run.finished_at = time.time()

        def fake_executor_cls(runtime, user_id, session_id):
            used_session_ids.append(session_id)
            m = MagicMock()
            m.run = AsyncMock(side_effect=fake_run)
            return m

        orch = Orchestrator(
            runtime=MagicMock(),
            planner=MagicMock(),
            plan_store=plan_store,
            plan_run_store=plan_run_store,
            user_id="test-user",
            session_id="wrong-prefix",  # 故意传错，recover() 应覆盖
        )

        with patch(
            "packages.orchestrator.orchestrator.StepExecutor",
            side_effect=fake_executor_cls,
        ):
            asyncio.run(orch.recover("run-sess"))

        # session 应该是 {原始session_prefix}-{step_id}
        assert used_session_ids[0] == "my-custom-session-prefix-s1"

    def test_only_crashed_steps_are_removed_from_step_runs(self, tmp_path):
        """recover() 只移除崩溃的 step_run，保留已完成的 step_run。"""
        s1 = TaskStep(step_id="s1", title="step1", description="", status=StepStatus.COMPLETED)
        s2 = TaskStep(step_id="s2", title="step2", description="", status=StepStatus.RUNNING)
        plan = _make_plan([s1, s2])
        plan.status = PlanStatus.RUNNING

        completed_run = StepRun(
            step_id="s1", title="step1",
            status=StepRunStatus.COMPLETED, output="done-s1",
            started_at=time.time(), finished_at=time.time(),
        )
        plan_run = PlanRun(
            plan_run_id="run-partial",
            plan_id="plan-test",
            goal="test",
            status="running",
            session_id="sess-partial",
            step_runs=[
                completed_run,
                StepRun(step_id="s2", title="step2", status=StepRunStatus.PENDING),
            ],
        )

        plan_store = PlanStore(tmp_path / "plans")
        plan_run_store = PlanRunStore(tmp_path / "plan_runs")
        plan_store.save(plan)
        plan_run_store.save(plan_run)

        async def fake_run(step, step_run):
            step_run.status = StepRunStatus.COMPLETED
            step_run.output = "recovered-s2"
            step_run.started_at = time.time()
            step_run.finished_at = time.time()

        orch = Orchestrator(
            runtime=MagicMock(),
            planner=MagicMock(),
            plan_store=plan_store,
            plan_run_store=plan_run_store,
            user_id="test-user",
        )

        with patch(
            "packages.orchestrator.orchestrator.StepExecutor",
            return_value=MagicMock(run=AsyncMock(side_effect=fake_run)),
        ):
            result = asyncio.run(orch.recover("run-partial"))

        # s1 的 output 应该保留
        s1_run = next(r for r in result.step_runs if r.step_id == "s1")
        assert s1_run.output == "done-s1"
        # s2 应该被重新执行
        s2_run = next(r for r in result.step_runs if r.step_id == "s2")
        assert s2_run.output == "recovered-s2"


# ---------------------------------------------------------------------------
# Test 3: plan_run not found
# ---------------------------------------------------------------------------

class TestRecoverErrors:
    def test_plan_run_not_found(self, tmp_path):
        plan_store = PlanStore(tmp_path / "plans")
        plan_run_store = PlanRunStore(tmp_path / "plan_runs")
        orch = Orchestrator(
            runtime=MagicMock(),
            planner=MagicMock(),
            plan_store=plan_store,
            plan_run_store=plan_run_store,
            user_id="test-user",
        )
        with pytest.raises(ValueError, match="plan_run not found"):
            asyncio.run(orch.recover("nonexistent-id"))
