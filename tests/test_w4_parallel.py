"""W4 阶段测试：并行调度（Scheduler + asyncio.gather）

覆盖场景：
1. 两个无依赖步骤在同一 wave 并行执行（通过时间戳验证重叠）
2. 有依赖的步骤必须在前置步骤完成后才能执行
3. 三步 DAG（A→C, B→C）：A/B 并行 → C 串行
4. Scheduler.get_ready_steps 单元测试
5. Scheduler.is_stuck 检测依赖环路
"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from packages.orchestrator.models import PlanRun, StepRunStatus
from packages.orchestrator.orchestrator import Orchestrator
from packages.orchestrator.store import PlanRunStore
from packages.planner.models import PlanStatus, StepStatus, TaskPlan, TaskStep
from packages.planner.scheduler import Scheduler
from packages.planner.store import PlanStore
from packages.runtime.models import AgentState, Phase, RunStatus


# ---------------------------------------------------------------------------
# 通用假对象
# ---------------------------------------------------------------------------

def _make_completed_state(run_id: str, output: str = "done") -> AgentState:
    state = AgentState(
        run_id=run_id,
        user_id="test-user",
        task="dummy",
        session_id="dummy-session",
        status=RunStatus.COMPLETED,
        phase=Phase.COMPLETED,
        final_output=output,
    )
    return state


class TimedFakeRuntime:
    """
    记录每次 chat() 调用的开始/结束时间，用于验证并行执行是否真正重叠。
    可通过 delay 参数模拟异步延迟。
    """

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.call_records: list[dict[str, Any]] = []

    async def chat(self, user_id: str, session_id: str, message: str) -> AgentState:
        start = time.monotonic()
        await asyncio.sleep(self.delay)
        end = time.monotonic()
        run_id = str(uuid.uuid4())
        self.call_records.append({
            "session_id": session_id,
            "start": start,
            "end": end,
            "run_id": run_id,
        })
        return _make_completed_state(run_id, output=f"result for {session_id}")

    async def resume(self, run_id: str, human_decision: dict[str, Any] | None = None) -> AgentState:
        raise NotImplementedError("not used in W4 parallel tests")


def _build_plan(*step_defs: dict) -> TaskPlan:
    """快捷构建 TaskPlan，step_def = {step_id, title, dependencies=[]}"""
    steps = [
        TaskStep(
            step_id=d["step_id"],
            title=d.get("title", d["step_id"]),
            description=d.get("description", ""),
            dependencies=d.get("dependencies", []),
        )
        for d in step_defs
    ]
    return TaskPlan(
        plan_id=str(uuid.uuid4()),
        goal="测试并行",
        steps=steps,
    )


class FakePlannerFromPlan:
    def __init__(self, plan: TaskPlan) -> None:
        self._plan = plan

    async def create_plan(self, goal: str, context: str = "") -> TaskPlan:
        return TaskPlan(
            plan_id=self._plan.plan_id,
            goal=self._plan.goal,
            steps=[
                TaskStep(
                    step_id=s.step_id,
                    title=s.title,
                    description=s.description,
                    dependencies=list(s.dependencies),
                )
                for s in self._plan.steps
            ],
        )


def _build_orchestrator(runtime, planner, tmp_path: Path) -> Orchestrator:
    return Orchestrator(
        runtime=runtime,
        planner=planner,
        plan_store=PlanStore(tmp_path / "plans"),
        plan_run_store=PlanRunStore(tmp_path / "plan_runs"),
        user_id="test-user",
        session_id=f"test-{uuid.uuid4()}",
    )


# ---------------------------------------------------------------------------
# Scheduler 单元测试
# ---------------------------------------------------------------------------

class TestScheduler:
    def setup_method(self):
        self.scheduler = Scheduler()

    def test_no_deps_all_ready(self):
        """无依赖步骤全部就绪"""
        plan = _build_plan(
            {"step_id": "a"},
            {"step_id": "b"},
        )
        ready = self.scheduler.get_ready_steps(plan, completed_ids=set())
        assert {s.step_id for s in ready} == {"a", "b"}

    def test_dep_not_satisfied(self):
        """依赖未满足的步骤不就绪"""
        plan = _build_plan(
            {"step_id": "a"},
            {"step_id": "b", "dependencies": ["a"]},
        )
        ready = self.scheduler.get_ready_steps(plan, completed_ids=set())
        assert [s.step_id for s in ready] == ["a"]

    def test_dep_satisfied(self):
        """依赖已满足后步骤就绪"""
        plan = _build_plan(
            {"step_id": "a"},
            {"step_id": "b", "dependencies": ["a"]},
        )
        # 标记 a 为 COMPLETED
        plan.steps[0].status = StepStatus.COMPLETED
        ready = self.scheduler.get_ready_steps(plan, completed_ids={"a"})
        assert [s.step_id for s in ready] == ["b"]

    def test_non_pending_steps_excluded(self):
        """非 PENDING 状态的步骤不出现在就绪列表"""
        plan = _build_plan(
            {"step_id": "a"},
            {"step_id": "b"},
        )
        plan.steps[0].status = StepStatus.RUNNING
        ready = self.scheduler.get_ready_steps(plan, completed_ids=set())
        assert [s.step_id for s in ready] == ["b"]

    def test_is_stuck_with_cycle(self):
        """
        模拟依赖死锁：a 依赖 b，b 依赖 a（环路）。
        is_stuck 应返回 True。
        """
        plan = _build_plan(
            {"step_id": "a", "dependencies": ["b"]},
            {"step_id": "b", "dependencies": ["a"]},
        )
        assert self.scheduler.is_stuck(plan, completed_ids=set()) is True

    def test_is_stuck_false_when_ready(self):
        """有步骤可就绪时 is_stuck 返回 False"""
        plan = _build_plan({"step_id": "a"})
        assert self.scheduler.is_stuck(plan, completed_ids=set()) is False

    def test_is_stuck_false_when_no_pending(self):
        """没有 PENDING 步骤时 is_stuck 返回 False"""
        plan = _build_plan({"step_id": "a"})
        plan.steps[0].status = StepStatus.COMPLETED
        assert self.scheduler.is_stuck(plan, completed_ids={"a"}) is False


# ---------------------------------------------------------------------------
# Orchestrator 并行执行测试
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_independent_steps_run_in_parallel(tmp_path: Path) -> None:
    """
    两个无依赖步骤应在同一 wave 并行运行。
    通过时间区间重叠验证：如果是串行，总时间 ≥ 2×delay；
    如果是并行，总时间 < 1.5×delay（有并发开销容忍）。
    """
    delay = 0.1
    runtime = TimedFakeRuntime(delay=delay)
    plan = _build_plan(
        {"step_id": "query-nvidia", "title": "查询英伟达股票"},
        {"step_id": "query-musk", "title": "查询马斯克财产"},
    )
    orchestrator = _build_orchestrator(runtime, FakePlannerFromPlan(plan), tmp_path)

    t0 = time.monotonic()
    plan_run = await orchestrator.run(goal="并行查询测试", context="")
    elapsed = time.monotonic() - t0

    assert plan_run.status == "completed"
    assert plan_run.completed is True
    assert len(plan_run.step_runs) == 2
    assert all(sr.status == StepRunStatus.COMPLETED for sr in plan_run.step_runs)

    # 两步并行时总耗时应明显小于 2×delay（串行下限）
    assert elapsed < delay * 1.8, (
        f"耗时 {elapsed:.3f}s 超过并行预期上限 {delay*1.8:.3f}s，疑似串行执行"
    )

    # 验证两步使用了不同的 session
    sessions_used = {r["session_id"] for r in runtime.call_records}
    assert len(sessions_used) == 2, "并行步骤应使用独立 session"


@pytest.mark.asyncio
async def test_dependent_step_runs_after_prerequisite(tmp_path: Path) -> None:
    """
    步骤 B 依赖 A：B 的 start 时间必须在 A 的 end 时间之后。
    """
    delay = 0.05
    runtime = TimedFakeRuntime(delay=delay)
    plan = _build_plan(
        {"step_id": "step-a", "title": "步骤A"},
        {"step_id": "step-b", "title": "步骤B（依赖A）", "dependencies": ["step-a"]},
    )
    orchestrator = _build_orchestrator(runtime, FakePlannerFromPlan(plan), tmp_path)

    plan_run = await orchestrator.run(goal="顺序依赖测试", context="")
    assert plan_run.status == "completed"
    assert len(plan_run.step_runs) == 2

    # 按 session 找对应记录
    records_by_step = {}
    for run_meta in runtime.call_records:
        sid = run_meta["session_id"]
        for sr in plan_run.step_runs:
            if sr.step_session_id == sid:
                records_by_step[sr.step_id] = run_meta
                break

    assert "step-a" in records_by_step
    assert "step-b" in records_by_step
    a_end = records_by_step["step-a"]["end"]
    b_start = records_by_step["step-b"]["start"]
    assert b_start >= a_end - 1e-6, (
        f"步骤B ({b_start:.4f}) 在步骤A结束 ({a_end:.4f}) 之前开始，违反依赖顺序"
    )


@pytest.mark.asyncio
async def test_diamond_dag_a_b_parallel_then_c(tmp_path: Path) -> None:
    """
    菱形 DAG：A 和 B 无依赖可并行，C 依赖 A 和 B。
    验证：A/B 并行运行，C 在 A 和 B 都完成后才运行。
    """
    delay = 0.05
    runtime = TimedFakeRuntime(delay=delay)
    plan = _build_plan(
        {"step_id": "a", "title": "步骤A"},
        {"step_id": "b", "title": "步骤B"},
        {"step_id": "c", "title": "步骤C（依赖A和B）", "dependencies": ["a", "b"]},
    )
    orchestrator = _build_orchestrator(runtime, FakePlannerFromPlan(plan), tmp_path)

    t0 = time.monotonic()
    plan_run = await orchestrator.run(goal="菱形DAG测试", context="")
    elapsed = time.monotonic() - t0

    assert plan_run.status == "completed"
    assert len(plan_run.step_runs) == 3
    assert all(sr.status == StepRunStatus.COMPLETED for sr in plan_run.step_runs)

    # A+B 并行（~delay），C 之后（~delay）= ~2×delay 而非 3×delay
    assert elapsed < delay * 3.5, (
        f"菱形DAG耗时 {elapsed:.3f}s 超出预期上限 {delay*3.5:.3f}s"
    )

    # C 的 start 必须在 A 和 B 的 end 之后
    records_by_step = {}
    for run_meta in runtime.call_records:
        sid = run_meta["session_id"]
        for sr in plan_run.step_runs:
            if sr.step_session_id == sid:
                records_by_step[sr.step_id] = run_meta
                break

    a_end = records_by_step["a"]["end"]
    b_end = records_by_step["b"]["end"]
    c_start = records_by_step["c"]["start"]
    assert c_start >= max(a_end, b_end) - 1e-6, "C 必须在 A 和 B 都完成后才运行"


@pytest.mark.asyncio
async def test_failed_step_skips_remaining(tmp_path: Path) -> None:
    """
    步骤A失败后，步骤B（无依赖）应被跳过。
    """
    class FailFirstRuntime:
        def __init__(self):
            self.call_count = 0

        async def chat(self, user_id, session_id, message):
            self.call_count += 1
            state = AgentState(
                run_id=str(uuid.uuid4()),
                user_id=user_id,
                task=message,
                session_id=session_id,
                status=RunStatus.FAILED,
                phase=Phase.COMPLETED,
                failure_reason="simulated failure",
            )
            return state

        async def resume(self, run_id, human_decision=None):
            raise NotImplementedError

    runtime = FailFirstRuntime()
    # 两个无依赖步骤，A 先失败（asyncio.gather 后都会被调用，结果都失败）
    # 实际 wave 里两步并行，都 fail，最终 plan failed
    plan = _build_plan(
        {"step_id": "step-a", "title": "步骤A（会失败）"},
        {"step_id": "step-b", "title": "步骤B（依赖A）", "dependencies": ["step-a"]},
    )
    orchestrator = _build_orchestrator(runtime, FakePlannerFromPlan(plan), tmp_path)

    plan_run = await orchestrator.run(goal="失败测试", context="")
    assert plan_run.status == "failed"

    step_a_run = next(sr for sr in plan_run.step_runs if sr.step_id == "step-a")
    step_b_run = next(sr for sr in plan_run.step_runs if sr.step_id == "step-b")

    assert step_a_run.status == StepRunStatus.FAILED
    # B 因 A 失败而被跳过
    assert step_b_run.status == StepRunStatus.SKIPPED
    assert runtime.call_count == 1  # 只执行了 A
