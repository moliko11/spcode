from __future__ import annotations

import asyncio
from pathlib import Path

from packages.orchestrator.models import PlanRun, StepRun, StepRunStatus
from packages.orchestrator.store import PlanRunStore
from packages.planner.models import TaskPlan, TaskStep
from packages.planner.store import PlanStore
from packages.tools import (
    TaskCreateTool,
    TaskListTool,
    TaskOutputTool,
    TaskStopTool,
    TaskUpdateTool,
    TodoWriteTool,
)
from packages.runtime.guardrail import GuardrailEngine, GuardrailViolation
from packages.workflow.models import WorkflowTaskStatus
from packages.workflow.store import WorkflowStore


def _stores(tmp_path: Path) -> tuple[PlanStore, PlanRunStore, WorkflowStore]:
    return PlanStore(tmp_path / "plans"), PlanRunStore(tmp_path / "plan_runs"), WorkflowStore(tmp_path / "workflows")


def test_task_tools_create_list_update_output_stop(tmp_path: Path) -> None:
    plan_store, plan_run_store, workflow_store = _stores(tmp_path)
    create = TaskCreateTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)
    list_tool = TaskListTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)
    update = TaskUpdateTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)
    output = TaskOutputTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)
    stop = TaskStopTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)

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
    workflow_id = created["workflow_id"]
    assert created["task_id"] == "task-a"
    assert created["plan_id"] == workflow_id

    listed = asyncio.run(list_tool.arun({"workflow_id": workflow_id}))
    assert listed["count"] == 1
    assert listed["tasks"][0]["status"] == "pending"

    updated = asyncio.run(
        update.arun(
            {
                "workflow_id": workflow_id,
                "task_id": "task-a",
                "status": "completed",
                "result_summary": "implemented",
                "evidence": [{"source_type": "test", "summary": "unit test"}],
            }
        )
    )
    assert updated["task"]["status"] == "completed"

    task_output = asyncio.run(output.arun({"workflow_id": workflow_id, "task_id": "task-a"}))
    assert task_output["output"] == "implemented"
    assert task_output["evidence"][0]["summary"] == "unit test"

    stopped = asyncio.run(stop.arun({"workflow_id": workflow_id, "reason": "no pending work"}))
    assert stopped["ok"] is True
    workflow = workflow_store.load(workflow_id)
    assert workflow is not None
    assert workflow.tasks[0].status == WorkflowTaskStatus.COMPLETED


def test_task_update_can_sync_plan_run(tmp_path: Path) -> None:
    plan_store, plan_run_store, workflow_store = _stores(tmp_path)
    create = TaskCreateTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)
    update = TaskUpdateTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)

    created = asyncio.run(create.arun({"task_id": "task-b", "title": "Run tests"}))
    workflow_id = created["workflow_id"]
    plan_run = PlanRun(plan_id=workflow_id, goal="ship", step_runs=[StepRun(step_id="task-b", title="Run tests")])
    plan_run_store.save(plan_run)

    asyncio.run(
        update.arun(
            {
                "workflow_id": workflow_id,
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


def test_task_create_rejects_missing_dependency(tmp_path: Path) -> None:
    plan_store, plan_run_store, workflow_store = _stores(tmp_path)
    create = TaskCreateTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)
    created = asyncio.run(create.arun({"task_id": "task-root", "title": "Root"}))

    try:
        asyncio.run(
            create.arun(
                {
                    "workflow_id": created["workflow_id"],
                    "task_id": "task-child",
                    "title": "Child",
                    "dependencies": ["missing-task"],
                }
            )
        )
    except ValueError as exc:
        assert "dependencies not found" in str(exc)
    else:
        raise AssertionError("expected missing dependency to fail")


def test_task_update_rejects_ambiguous_task_id(tmp_path: Path) -> None:
    plan_store, plan_run_store, workflow_store = _stores(tmp_path)
    create = TaskCreateTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)
    update = TaskUpdateTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)
    asyncio.run(create.arun({"task_id": "same-id", "title": "First"}))
    asyncio.run(create.arun({"task_id": "same-id", "title": "Second"}))

    try:
        asyncio.run(update.arun({"task_id": "same-id", "status": "running"}))
    except ValueError as exc:
        assert "provide workflow_id" in str(exc)
    else:
        raise AssertionError("expected ambiguous task_id to fail")


def test_task_update_rejects_invalid_terminal_transition(tmp_path: Path) -> None:
    plan_store, plan_run_store, workflow_store = _stores(tmp_path)
    create = TaskCreateTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)
    update = TaskUpdateTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)
    created = asyncio.run(create.arun({"task_id": "task-c", "title": "Complete"}))
    asyncio.run(update.arun({"workflow_id": created["workflow_id"], "task_id": "task-c", "status": "completed"}))

    try:
        asyncio.run(update.arun({"workflow_id": created["workflow_id"], "task_id": "task-c", "status": "running"}))
    except ValueError as exc:
        assert "invalid task status transition" in str(exc)
    else:
        raise AssertionError("expected invalid transition to fail")


def test_todo_write_creates_visible_workflow_todos(tmp_path: Path) -> None:
    plan_store, plan_run_store, workflow_store = _stores(tmp_path)
    todo_write = TodoWriteTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)

    result = asyncio.run(
        todo_write.arun(
            {
                "goal": "ship m1",
                "todos": [
                    {"id": "inspect", "content": "Inspect current task tools", "status": "completed"},
                    {"id": "migrate", "content": "Migrate to WorkflowStore", "status": "in_progress", "priority": "high"},
                ],
            }
        )
    )

    workflow = workflow_store.load(result["workflow_id"])
    assert workflow is not None
    assert result["count"] == 2
    assert workflow.tasks[0].status == WorkflowTaskStatus.COMPLETED
    assert workflow.tasks[1].status == WorkflowTaskStatus.RUNNING
    assert workflow.tasks[1].metadata["priority"] == "high"

    updated = asyncio.run(
        todo_write.arun(
            {
                "workflow_id": result["workflow_id"],
                "todos": [
                    {"id": "migrate", "content": "Migrate task tools", "status": "completed"},
                ],
            }
        )
    )
    assert updated["todos"][1]["status"] == "completed"


def test_task_list_imports_legacy_plan_steps_into_workflow(tmp_path: Path) -> None:
    plan_store, plan_run_store, workflow_store = _stores(tmp_path)
    plan = TaskPlan(goal="legacy plan", steps=[TaskStep(step_id="legacy-task", title="Legacy task", description="")])
    plan_store.save(plan)
    list_tool = TaskListTool(plan_store=plan_store, plan_run_store=plan_run_store, workflow_store=workflow_store)

    listed = asyncio.run(list_tool.arun({"plan_id": plan.plan_id}))

    assert listed["count"] == 1
    assert listed["tasks"][0]["task_id"] == "legacy-task"
    imported = workflow_store.load(plan.plan_id)
    assert imported is not None
    assert imported.metadata["imported_from_plan"] is True


def test_guardrail_validates_task_tool_args() -> None:
    guardrail = GuardrailEngine()

    guardrail.validate_tool_args("task_create", {"title": "Task"})
    guardrail.validate_tool_args("todo_write", {"todos": [{"content": "Task", "status": "pending"}]})
    try:
        guardrail.validate_tool_args("task_create", {"title": ""})
    except GuardrailViolation as exc:
        assert "non-empty" in str(exc)
    else:
        raise AssertionError("expected empty title to fail")

    try:
        guardrail.validate_tool_args("todo_write", {"todos": [{"content": "Task", "status": "unknown"}]})
    except GuardrailViolation as exc:
        assert "status" in str(exc)
    else:
        raise AssertionError("expected invalid todo status to fail")
