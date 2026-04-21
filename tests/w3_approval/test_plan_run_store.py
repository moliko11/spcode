from __future__ import annotations

from packages.orchestrator.models import PlanRun, StepRun, StepRunStatus
from packages.orchestrator.store import PlanRunStore


def test_plan_run_store_roundtrip(tmp_path):
    store = PlanRunStore(tmp_path / "plan_runs")
    plan_run = PlanRun(
        plan_id="plan-1",
        goal="goal",
        status="waiting_human",
        session_id="sess-1",
        current_step_index=1,
        pending_step_id="step_2",
        step_runs=[
            StepRun(step_id="step_1", title="A", status=StepRunStatus.COMPLETED, output="ok"),
            StepRun(
                step_id="step_2",
                title="B",
                status=StepRunStatus.WAITING_HUMAN,
                run_id="run-2",
                pending_human_request={"context": {"tool_name": "file_write"}},
            ),
        ],
    )

    store.save(plan_run)
    loaded = store.load(plan_run.plan_run_id)

    assert loaded is not None
    assert loaded.plan_id == "plan-1"
    assert loaded.status == "waiting_human"
    assert loaded.session_id == "sess-1"
    assert loaded.pending_step_id == "step_2"
    assert loaded.step_runs[1].status == StepRunStatus.WAITING_HUMAN
    assert loaded.step_runs[1].pending_human_request["context"]["tool_name"] == "file_write"
