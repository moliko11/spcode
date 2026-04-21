"""W2 阶段 — Orchestrator 数据模型测试"""
from __future__ import annotations

import time

from packages.orchestrator.models import PlanRun, StepRun, StepRunStatus


class TestStepRun:
    def test_defaults(self):
        sr = StepRun(step_id="s1", title="t")
        assert sr.status == StepRunStatus.PENDING
        assert sr.run_id is None
        assert sr.output is None
        assert sr.error is None
        assert sr.duration_s is None

    def test_duration(self):
        sr = StepRun(step_id="s1", title="t")
        sr.started_at = 1000.0
        sr.finished_at = 1002.5
        assert sr.duration_s == 2.5

    def test_to_dict(self):
        sr = StepRun(step_id="s1", title="t", status=StepRunStatus.COMPLETED, output="ok")
        d = sr.to_dict()
        assert d["step_id"] == "s1"
        assert d["status"] == "completed"
        assert d["output"] == "ok"


class TestPlanRun:
    def test_defaults(self):
        pr = PlanRun()
        assert pr.plan_run_id
        assert pr.step_runs == []
        assert not pr.completed

    def test_to_dict(self):
        pr = PlanRun(plan_id="p1", goal="g")
        pr.step_runs.append(StepRun(step_id="s1", title="t", status=StepRunStatus.COMPLETED))
        d = pr.to_dict()
        assert d["plan_id"] == "p1"
        assert d["goal"] == "g"
        assert len(d["step_runs"]) == 1
        assert d["step_runs"][0]["status"] == "completed"
