"""W1 阶段 — PlanStore 持久化测试"""
from __future__ import annotations

import pytest

from packages.planner.models import PlanStatus, TaskPlan, TaskStep
from packages.planner.store import PlanStore


@pytest.fixture
def store(tmp_path):
    return PlanStore(tmp_path / "plans")


def _make_plan(goal: str = "目标") -> TaskPlan:
    return TaskPlan(
        goal=goal,
        steps=[
            TaskStep(step_id="step_1", title="A", description="do A"),
            TaskStep(step_id="step_2", title="B", description="do B", dependencies=["step_1"]),
        ],
    )


class TestPlanStore:
    def test_save_and_load(self, store):
        plan = _make_plan()
        store.save(plan)
        loaded = store.load(plan.plan_id)
        assert loaded is not None
        assert loaded.plan_id == plan.plan_id
        assert loaded.goal == "目标"
        assert len(loaded.steps) == 2

    def test_load_missing_returns_none(self, store):
        assert store.load("nonexistent-id") is None

    def test_overwrite(self, store):
        plan = _make_plan()
        store.save(plan)
        plan.status = PlanStatus.APPROVED
        store.save(plan)
        loaded = store.load(plan.plan_id)
        assert loaded.status == PlanStatus.APPROVED

    def test_list_all_empty(self, store):
        assert store.list_all() == []

    def test_list_all_multiple(self, store):
        import time
        p1 = _make_plan("目标1")
        time.sleep(0.01)
        p2 = _make_plan("目标2")
        store.save(p2)
        store.save(p1)
        plans = store.list_all()
        assert len(plans) == 2
        # 按 created_at 升序
        assert plans[0].created_at <= plans[1].created_at

    def test_steps_roundtrip(self, store):
        plan = _make_plan()
        store.save(plan)
        loaded = store.load(plan.plan_id)
        assert loaded.steps[1].dependencies == ["step_1"]
        assert loaded.steps[0].acceptance_criteria == []
