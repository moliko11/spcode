"""W1 阶段 — Planner 数据模型测试"""
from __future__ import annotations

import time

import pytest

from packages.planner.models import (
    PlanStatus,
    StepStatus,
    TaskPlan,
    TaskStep,
)


# ---------------------------------------------------------------------------
# TaskStep
# ---------------------------------------------------------------------------

class TestTaskStep:
    def test_defaults(self):
        step = TaskStep(step_id="s1", title="t", description="d")
        assert step.status == StepStatus.PENDING
        assert step.dependencies == []
        assert step.acceptance_criteria == []
        assert step.suggested_tools == []
        assert step.output is None
        assert step.error is None

    def test_roundtrip(self):
        step = TaskStep(
            step_id="s1",
            title="创建文件",
            description="写一个 hello.py",
            dependencies=["s0"],
            acceptance_criteria=["文件存在"],
            suggested_tools=["FileWriteTool"],
            status=StepStatus.COMPLETED,
            output="done",
        )
        d = step.to_dict()
        restored = TaskStep.from_dict(d)
        assert restored.step_id == "s1"
        assert restored.status == StepStatus.COMPLETED
        assert restored.output == "done"
        assert restored.dependencies == ["s0"]
        assert restored.acceptance_criteria == ["文件存在"]

    def test_from_dict_missing_optional_fields(self):
        """from_dict 应能处理缺少可选字段的旧数据"""
        d = {"step_id": "s1", "title": "t", "description": "d"}
        step = TaskStep.from_dict(d)
        assert step.dependencies == []
        assert step.status == StepStatus.PENDING


# ---------------------------------------------------------------------------
# TaskPlan
# ---------------------------------------------------------------------------

class TestTaskPlan:
    def _make_plan(self) -> TaskPlan:
        steps = [
            TaskStep(step_id="step_1", title="A", description="do A"),
            TaskStep(step_id="step_2", title="B", description="do B", dependencies=["step_1"]),
        ]
        return TaskPlan(goal="test goal", steps=steps)

    def test_defaults(self):
        plan = TaskPlan()
        assert plan.status == PlanStatus.DRAFT
        assert plan.steps == []
        assert plan.plan_id  # auto-generated uuid

    def test_roundtrip(self):
        plan = self._make_plan()
        d = plan.to_dict()
        restored = TaskPlan.from_dict(d)
        assert restored.plan_id == plan.plan_id
        assert restored.goal == "test goal"
        assert len(restored.steps) == 2
        assert restored.steps[1].dependencies == ["step_1"]
        assert restored.status == PlanStatus.DRAFT

    def test_timestamps(self):
        before = time.time()
        plan = TaskPlan(goal="g")
        after = time.time()
        assert before <= plan.created_at <= after
        assert before <= plan.updated_at <= after
