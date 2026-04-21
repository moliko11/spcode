"""W2 阶段 — Orchestrator 集成测试（全 mock）"""
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mock_llm_with_plan(steps_data: list[dict]) -> MagicMock:
    content = json.dumps({"goal": "测试目标", "steps": steps_data})
    resp = MagicMock()
    resp.content = content
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


def _agent_state(status: RunStatus = RunStatus.COMPLETED, output: str = "ok") -> MagicMock:
    s = MagicMock(spec=AgentState)
    s.run_id = "run-x"
    s.status = status
    s.final_output = output
    s.failure_reason = None if status == RunStatus.COMPLETED else "error"
    return s


def _two_step_llm() -> MagicMock:
    return _mock_llm_with_plan([
        {"step_id": "step_1", "title": "步骤1", "description": "d1",
         "dependencies": [], "acceptance_criteria": ["c1"], "suggested_tools": []},
        {"step_id": "step_2", "title": "步骤2", "description": "d2",
         "dependencies": ["step_1"], "acceptance_criteria": ["c2"], "suggested_tools": []},
    ])


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_all_steps_complete(self, tmp_path):
        llm = _two_step_llm()
        runtime = MagicMock()
        runtime.chat = AsyncMock(side_effect=[
            _agent_state(RunStatus.COMPLETED, "output1"),
            _agent_state(RunStatus.COMPLETED, "output2"),
        ])
        orch = Orchestrator(
            runtime=runtime,
            planner=Planner(llm=llm),
            plan_store=PlanStore(tmp_path / "plans"),
            plan_run_store=PlanRunStore(tmp_path / "plan_runs"),
        )
        plan_run = await orch.run("测试目标")

        assert plan_run.completed
        assert len(plan_run.step_runs) == 2
        assert all(sr.status == StepRunStatus.COMPLETED for sr in plan_run.step_runs)
        assert plan_run.finished_at is not None

    @pytest.mark.asyncio
    async def test_first_step_fails_skips_next(self, tmp_path):
        llm = _two_step_llm()
        runtime = MagicMock()
        runtime.chat = AsyncMock(return_value=_agent_state(RunStatus.FAILED))
        orch = Orchestrator(
            runtime=runtime,
            planner=Planner(llm=llm),
            plan_store=PlanStore(tmp_path / "plans"),
            plan_run_store=PlanRunStore(tmp_path / "plan_runs"),
        )
        plan_run = await orch.run("测试目标")

        assert not plan_run.completed
        assert plan_run.step_runs[0].status == StepRunStatus.FAILED
        assert plan_run.step_runs[1].status == StepRunStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_topology_order_respected(self, tmp_path):
        """步骤 step_2 依赖 step_1，应在 step_1 之后执行"""
        call_order: list[str] = []

        llm = _two_step_llm()

        async def fake_chat(user_id, session_id, message):
            # 通过 message 内容判断是哪个 step
            if "步骤1" in message:
                call_order.append("step_1")
            else:
                call_order.append("step_2")
            return _agent_state()

        runtime = MagicMock()
        runtime.chat = fake_chat

        orch = Orchestrator(
            runtime=runtime,
            planner=Planner(llm=llm),
            plan_store=PlanStore(tmp_path / "plans"),
            plan_run_store=PlanRunStore(tmp_path / "plan_runs"),
        )
        await orch.run("测试目标")
        assert call_order == ["step_1", "step_2"]

    @pytest.mark.asyncio
    async def test_plan_saved_to_store(self, tmp_path):
        llm = _two_step_llm()
        runtime = MagicMock()
        runtime.chat = AsyncMock(return_value=_agent_state())
        store = PlanStore(tmp_path / "plans")
        orch = Orchestrator(
            runtime=runtime,
            planner=Planner(llm=llm),
            plan_store=store,
            plan_run_store=PlanRunStore(tmp_path / "plan_runs"),
        )

        plan_run = await orch.run("测试目标")

        # plan 文件应已写入磁盘
        plans = store.list_all()
        assert len(plans) == 1
        assert plans[0].plan_id == plan_run.plan_id

    @pytest.mark.asyncio
    async def test_final_output_summary(self, tmp_path):
        llm = _two_step_llm()
        runtime = MagicMock()
        runtime.chat = AsyncMock(side_effect=[
            _agent_state(output="创建了文件"),
            _agent_state(output="运行成功"),
        ])
        orch = Orchestrator(
            runtime=runtime,
            planner=Planner(llm=llm),
            plan_store=PlanStore(tmp_path / "plans"),
            plan_run_store=PlanRunStore(tmp_path / "plan_runs"),
        )
        plan_run = await orch.run("测试目标")

        assert "步骤1" in plan_run.final_output
        assert "步骤2" in plan_run.final_output
        assert "创建了文件" in plan_run.final_output
