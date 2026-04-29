from __future__ import annotations

import asyncio
from pathlib import Path

from packages.orchestrator.models import PlanRun, StepRun, StepRunStatus
from packages.orchestrator.store import PlanRunStore
from packages.planner.models import StepStatus
from packages.planner.store import PlanStore
from packages.tools import (
    TaskCreateTool,
    TaskListTool,
    TaskOutputTool,
    TaskStopTool,
    TaskUpdateTool,
)


def _stores(tmp_path: Path) -> tuple[PlanStore, PlanRunStore]:
    return PlanStore(tmp_path / "plans"), PlanRunStore(tmp_path / "plan_runs")


def test_task_tools_create_list_update_output_stop(tmp_path: Path) -> None:
    plan_store, plan_run_store = _stores(tmp_path)
    create = TaskCreateTool(plan_store=plan_store, plan_run_store=plan_run_store)
    list_tool = TaskListTool(plan_store=plan_store, plan_run_store=plan_run_store)
    update = TaskUpdateTool(plan_store=plan_store, plan_run_store=plan_run_store)
    output = TaskOutputTool(plan_store=plan_store, plan_run_store=plan_run_store)
    stop = TaskStopTool(plan_store=plan_store, plan_run_store=plan_run_store)

    created = asyncio.run(
        create.arun(
            {
                "goal": "ship task tools",
                "task_id": "task-a",
                "title": "Implement task tools",
                "description": "Create workflow task tools",
                "acceptance_criteria": ["tools can persist task state"],
                "target_files": ["packages/tools/task_tools.py"],
            }
        )
    )
    plan_id = created["plan_id"]
    assert created["task_id"] == "task-a"

    listed = asyncio.run(list_tool.arun({"plan_id": plan_id}))
    assert listed["count"] == 1
    assert listed["tasks"][0]["status"] == "pending"

    updated = asyncio.run(
        update.arun(
            {
                "plan_id": plan_id,
                "task_id": "task-a",
                "status": "completed",
                "result_summary": "implemented",
                "evidence": [{"source_type": "test", "summary": "unit test"}],
            }
        )
    )
    assert updated["task"]["status"] == "completed"

    task_output = asyncio.run(output.arun({"plan_id": plan_id, "task_id": "task-a"}))
    assert task_output["output"] == "implemented"
    assert task_output["evidence"][0]["summary"] == "unit test"

    stopped = asyncio.run(stop.arun({"plan_id": plan_id, "reason": "no pending work"}))
    assert stopped["ok"] is True
    plan = plan_store.load(plan_id)
    assert plan is not None
    assert plan.steps[0].status == StepStatus.COMPLETED


def test_task_update_can_sync_plan_run(tmp_path: Path) -> None:
    plan_store, plan_run_store = _stores(tmp_path)
    create = TaskCreateTool(plan_store=plan_store, plan_run_store=plan_run_store)
    update = TaskUpdateTool(plan_store=plan_store, plan_run_store=plan_run_store)

    created = asyncio.run(create.arun({"task_id": "task-b", "title": "Run tests"}))
    plan_id = created["plan_id"]
    plan_run = PlanRun(plan_id=plan_id, goal="ship", step_runs=[StepRun(step_id="task-b", title="Run tests")])
    plan_run_store.save(plan_run)

    asyncio.run(
        update.arun(
            {
                "plan_id": plan_id,
                "plan_run_id": plan_run.plan_run_id,
                "task_id": "task-b",
                "status": "completed",
                "result_summary": "tests passed",
            }
        )
    )

    loaded = plan_run_store.load(plan_run.plan_run_id)
    assert loaded is not None
    assert loaded.step_runs[0].status == StepRunStatus.COMPLETED
    assert loaded.step_runs[0].output == "tests passed"
