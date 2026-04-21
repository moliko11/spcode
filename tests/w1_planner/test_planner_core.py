"""W1 阶段 — Planner LLM 调用与 JSON 解析测试"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from packages.planner.models import PlanStatus, StepStatus
from packages.planner.planner import Planner, PlannerError


def _mock_llm(content: str) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


def _valid_plan_json(n_steps: int = 2) -> str:
    steps = []
    for i in range(1, n_steps + 1):
        steps.append({
            "step_id": f"step_{i}",
            "title": f"步骤 {i}",
            "description": f"描述 {i}",
            "dependencies": [f"step_{i-1}"] if i > 1 else [],
            "acceptance_criteria": [f"完成条件 {i}"],
            "suggested_tools": ["BashTool"],
        })
    return json.dumps({"goal": "测试目标", "steps": steps})


# ---------------------------------------------------------------------------

class TestPlanner:
    @pytest.mark.asyncio
    async def test_create_plan_basic(self):
        llm = _mock_llm(_valid_plan_json(2))
        planner = Planner(llm=llm)
        plan = await planner.create_plan("测试目标")

        assert plan.goal == "测试目标"
        assert plan.status == PlanStatus.DRAFT
        assert len(plan.steps) == 2
        assert plan.steps[0].step_id == "step_1"
        assert plan.steps[0].dependencies == []
        assert plan.steps[1].dependencies == ["step_1"]
        assert plan.steps[0].acceptance_criteria == ["完成条件 1"]

    @pytest.mark.asyncio
    async def test_create_plan_strips_markdown_fences(self):
        """LLM 有时会用 ```json 包裹，应自动剥离"""
        raw = "```json\n" + _valid_plan_json(1) + "\n```"
        llm = _mock_llm(raw)
        plan = await Planner(llm=llm).create_plan("目标")
        assert len(plan.steps) == 1

    @pytest.mark.asyncio
    async def test_create_plan_with_context(self):
        llm = _mock_llm(_valid_plan_json(1))
        planner = Planner(llm=llm)
        plan = await planner.create_plan("目标", context="背景信息")
        # context 存入 plan
        assert plan.context == "背景信息"
        # 确认 ainvoke 被调用，且 prompt 包含背景信息
        call_args = llm.ainvoke.call_args[0][0]  # messages list
        user_msg_content = call_args[1].content
        assert "背景信息" in user_msg_content

    @pytest.mark.asyncio
    async def test_create_plan_invalid_json_raises(self):
        llm = _mock_llm("这不是 JSON")
        with pytest.raises(PlannerError, match="无法解析"):
            await Planner(llm=llm).create_plan("目标")

    @pytest.mark.asyncio
    async def test_create_plan_empty_steps_raises(self):
        llm = _mock_llm(json.dumps({"goal": "目标", "steps": []}))
        with pytest.raises(PlannerError, match="没有任何步骤"):
            await Planner(llm=llm).create_plan("目标")

    @pytest.mark.asyncio
    async def test_step_id_fallback(self):
        """step_id 缺失时自动补全"""
        data = {
            "goal": "g",
            "steps": [
                {"title": "t", "description": "d"},
            ],
        }
        llm = _mock_llm(json.dumps(data))
        plan = await Planner(llm=llm).create_plan("g")
        assert plan.steps[0].step_id == "step_1"
