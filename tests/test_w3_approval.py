"""W3 阶段测试：审批暂停与恢复

覆盖三个场景：
1. 审批通过 → 步骤继续执行并完成
2. 审批拒绝 → plan_run 进入 failed 状态
3. 编辑参数 → edited_arguments 正确传递给 AgentRuntime
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from packages.orchestrator.models import PlanRun, StepRunStatus
from packages.orchestrator.orchestrator import Orchestrator
from packages.orchestrator.store import PlanRunStore
from packages.planner.models import PlanStatus, StepStatus, TaskPlan, TaskStep
from packages.planner.store import PlanStore
from packages.runtime.models import AgentState, Phase, RunStatus


# ---------------------------------------------------------------------------
# 通用假对象
# ---------------------------------------------------------------------------

class FakePlanner:
    """不调用 LLM，直接返回预置的 TaskPlan。"""

    def __init__(self, plan: TaskPlan) -> None:
        self._plan = plan

    async def create_plan(self, goal: str, context: str = "") -> TaskPlan:
        # 每次返回一个新副本（避免 test 之间状态共享）
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


def _make_waiting_state(run_id: str) -> AgentState:
    """返回一个 WAITING_HUMAN 状态的 AgentState。"""
    state = AgentState(
        run_id=run_id,
        user_id="test-user",
        task="dummy",
        session_id="dummy-session",
        status=RunStatus.WAITING_HUMAN,
        phase=Phase.WAITING_HUMAN,
    )
    state.pending_human_request = {
        "tool_name": "file_write",
        "arguments": {"path": "out.txt", "content": "hello"},
        "reason": "需要审批",
    }
    return state


def _make_completed_state(run_id: str, output: str = "done") -> AgentState:
    """返回一个 COMPLETED 状态的 AgentState。"""
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


def _make_rejected_state(run_id: str) -> AgentState:
    """返回一个被拒绝后（审批拒绝时 agent_loop 会设置 COMPLETED）的 AgentState。"""
    state = AgentState(
        run_id=run_id,
        user_id="test-user",
        task="dummy",
        session_id="dummy-session",
        status=RunStatus.COMPLETED,
        phase=Phase.COMPLETED,
        final_output="human approval rejected; execution stopped",
    )
    return state


class FakeRuntime:
    """
    可编程的 AgentRuntime 假对象。

    通过 run_id 跟踪 WAITING_HUMAN 状态；
    resume() 根据 approved 标志返回对应状态。
    """

    def __init__(self) -> None:
        # run_id → 当前等待审批的状态
        self._pending: dict[str, AgentState] = {}
        # 记录 resume 调用参数，用于断言
        self.resume_calls: list[dict[str, Any]] = []
        # 当 chat() 被调用时返回什么（list of AgentState）
        self._chat_responses: list[AgentState] = []
        self._chat_index = 0

    def push_chat_response(self, state: AgentState) -> None:
        self._chat_responses.append(state)

    async def chat(self, user_id: str, session_id: str, message: str) -> AgentState:
        if self._chat_index >= len(self._chat_responses):
            raise AssertionError("FakeRuntime.chat: no more responses")
        state = self._chat_responses[self._chat_index]
        self._chat_index += 1
        if state.status == RunStatus.WAITING_HUMAN:
            self._pending[state.run_id] = state
        return state

    async def resume(self, run_id: str, human_decision: dict[str, Any] | None = None) -> AgentState:
        self.resume_calls.append({"run_id": run_id, "human_decision": human_decision})
        approved = bool((human_decision or {}).get("approved", False))
        if approved:
            return _make_completed_state(run_id, output="approved and done")
        return _make_rejected_state(run_id)


def _build_single_step_plan(step_id: str = "step-1") -> TaskPlan:
    return TaskPlan(
        plan_id=str(uuid.uuid4()),
        goal="测试目标",
        steps=[
            TaskStep(
                step_id=step_id,
                title="测试步骤",
                description="做一些需要审批的操作",
            )
        ],
    )


def _build_orchestrator(
    fake_runtime: FakeRuntime,
    fake_planner: FakePlanner,
    tmp_path: Path,
) -> Orchestrator:
    plan_store = PlanStore(tmp_path / "plans")
    plan_run_store = PlanRunStore(tmp_path / "plan_runs")
    return Orchestrator(
        runtime=fake_runtime,
        planner=fake_planner,
        plan_store=plan_store,
        plan_run_store=plan_run_store,
        user_id="test-user",
        session_id=f"test-session-{uuid.uuid4()}",
    )


# ---------------------------------------------------------------------------
# 测试 1：审批通过 → 步骤完成，plan 状态为 completed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approval_accepted_completes_execution(tmp_path: Path) -> None:
    """审批通过后，步骤应继续执行并使 plan_run 进入 completed 状态。"""
    run_id = str(uuid.uuid4())

    # 第一次 chat() 返回 WAITING_HUMAN
    fake_runtime = FakeRuntime()
    fake_runtime.push_chat_response(_make_waiting_state(run_id))

    plan = _build_single_step_plan()
    orchestrator = _build_orchestrator(fake_runtime, FakePlanner(plan), tmp_path)

    # 执行直到暂停
    plan_run = await orchestrator.run(goal="测试目标", context="")
    assert plan_run.status == "waiting_human"
    assert plan_run.pending_step_id == "step-1"
    assert len(plan_run.step_runs) == 1
    assert plan_run.step_runs[0].status == StepRunStatus.WAITING_HUMAN

    # 审批通过
    plan_run = await orchestrator.resume(
        plan_run_id=plan_run.plan_run_id,
        approved=True,
        approved_by="tester",
    )
    assert plan_run.status == "completed"
    assert plan_run.completed is True
    assert plan_run.pending_step_id is None
    # step 应该被标记为 COMPLETED
    step = plan_run.step_runs[0]
    assert step.status == StepRunStatus.COMPLETED
    assert step.output == "approved and done"
    # resume 应被调用一次
    assert len(fake_runtime.resume_calls) == 1
    assert fake_runtime.resume_calls[0]["human_decision"]["approved"] is True
    assert fake_runtime.resume_calls[0]["human_decision"]["approved_by"] == "tester"


# ---------------------------------------------------------------------------
# 测试 2：审批拒绝 → plan_run 进入 failed 状态
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approval_rejected_fails_plan(tmp_path: Path) -> None:
    """审批拒绝后，plan_run 应进入 failed 状态，步骤状态为 FAILED。"""
    run_id = str(uuid.uuid4())

    fake_runtime = FakeRuntime()
    fake_runtime.push_chat_response(_make_waiting_state(run_id))

    plan = _build_single_step_plan()
    orchestrator = _build_orchestrator(fake_runtime, FakePlanner(plan), tmp_path)

    plan_run = await orchestrator.run(goal="测试目标", context="")
    assert plan_run.status == "waiting_human"

    # 拒绝审批
    plan_run = await orchestrator.resume(
        plan_run_id=plan_run.plan_run_id,
        approved=False,
        approved_by="tester",
    )
    assert plan_run.status == "failed"
    assert plan_run.completed is False
    step = plan_run.step_runs[0]
    assert step.status == StepRunStatus.FAILED
    assert "rejected" in (step.error or "").lower()


# ---------------------------------------------------------------------------
# 测试 3：编辑参数 → edited_arguments 正确传递给 runtime.resume()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_arguments_passed_to_runtime(tmp_path: Path) -> None:
    """
    用户在审批时修改了参数，edited_arguments 应原样传递给 AgentRuntime.resume()。
    """
    run_id = str(uuid.uuid4())

    fake_runtime = FakeRuntime()
    fake_runtime.push_chat_response(_make_waiting_state(run_id))

    plan = _build_single_step_plan()
    orchestrator = _build_orchestrator(fake_runtime, FakePlanner(plan), tmp_path)

    plan_run = await orchestrator.run(goal="测试目标", context="")
    assert plan_run.status == "waiting_human"

    edited = {"path": "modified.txt", "content": "edited content"}
    plan_run = await orchestrator.resume(
        plan_run_id=plan_run.plan_run_id,
        approved=True,
        approved_by="tester",
        edited_arguments=edited,
    )

    assert plan_run.status == "completed"
    # 检查 runtime 收到的参数
    assert len(fake_runtime.resume_calls) == 1
    decision = fake_runtime.resume_calls[0]["human_decision"]
    assert decision["edited_arguments"] == edited
    assert decision["approved"] is True


# ---------------------------------------------------------------------------
# 测试 4：在非 waiting_human 状态调用 resume → 抛出 ValueError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resume_non_waiting_plan_raises(tmp_path: Path) -> None:
    """对非 waiting_human 状态的 plan_run 调用 resume，应抛出 ValueError。"""
    run_id = str(uuid.uuid4())

    # chat() 直接返回 COMPLETED（无需审批的步骤）
    fake_runtime = FakeRuntime()
    fake_runtime.push_chat_response(_make_completed_state(run_id, output="直接完成"))

    plan = _build_single_step_plan()
    orchestrator = _build_orchestrator(fake_runtime, FakePlanner(plan), tmp_path)

    plan_run = await orchestrator.run(goal="测试目标", context="")
    assert plan_run.status == "completed"

    # 尝试对已完成的 plan_run resume，应报错
    with pytest.raises(ValueError, match="not waiting for human approval"):
        await orchestrator.resume(
            plan_run_id=plan_run.plan_run_id,
            approved=True,
        )
