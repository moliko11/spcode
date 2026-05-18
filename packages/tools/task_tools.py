from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from packages.orchestrator.models import PlanRun, StepRun, StepRunStatus
from packages.orchestrator.store import PlanRunStore
from packages.planner.store import PlanStore
from packages.runtime.config import PLAN_RUNS_DIR, PLANS_DIR, WORKFLOWS_DIR
from packages.workflow.models import WorkflowRun, WorkflowStatus, WorkflowTask, WorkflowTaskStatus
from packages.workflow.service import WorkflowService
from packages.workflow.store import WorkflowStore


def _step_run_status(status: WorkflowTaskStatus) -> StepRunStatus:
    mapping = {
        WorkflowTaskStatus.PENDING: StepRunStatus.PENDING,
        WorkflowTaskStatus.READY: StepRunStatus.PENDING,
        WorkflowTaskStatus.RUNNING: StepRunStatus.RUNNING,
        WorkflowTaskStatus.WAITING_HUMAN: StepRunStatus.WAITING_HUMAN,
        WorkflowTaskStatus.COMPLETED: StepRunStatus.COMPLETED,
        WorkflowTaskStatus.FAILED: StepRunStatus.FAILED,
        WorkflowTaskStatus.SKIPPED: StepRunStatus.SKIPPED,
        WorkflowTaskStatus.BLOCKED: StepRunStatus.SKIPPED,
        WorkflowTaskStatus.CANCELLED: StepRunStatus.SKIPPED,
    }
    return mapping[status]


def _todo_status(value: str) -> WorkflowTaskStatus:
    normalized = value.strip().lower().replace("-", "_")
    mapping = {
        "pending": WorkflowTaskStatus.PENDING,
        "todo": WorkflowTaskStatus.PENDING,
        "in_progress": WorkflowTaskStatus.RUNNING,
        "running": WorkflowTaskStatus.RUNNING,
        "completed": WorkflowTaskStatus.COMPLETED,
        "done": WorkflowTaskStatus.COMPLETED,
        "blocked": WorkflowTaskStatus.BLOCKED,
        "cancelled": WorkflowTaskStatus.CANCELLED,
        "canceled": WorkflowTaskStatus.CANCELLED,
        "skipped": WorkflowTaskStatus.SKIPPED,
    }
    return mapping.get(normalized, WorkflowTaskStatus.PENDING)


class _TaskToolBase:
    def __init__(
        self,
        plan_store: PlanStore | None = None,
        plan_run_store: PlanRunStore | None = None,
        workflow_store: WorkflowStore | None = None,
        *,
        plans_dir: Path = PLANS_DIR,
        plan_runs_dir: Path = PLAN_RUNS_DIR,
        workflows_dir: Path = WORKFLOWS_DIR,
    ) -> None:
        self.plan_store = plan_store or PlanStore(plans_dir)
        self.plan_run_store = plan_run_store or PlanRunStore(plan_runs_dir)
        self.workflow_store = workflow_store or WorkflowStore(workflows_dir)
        self.workflow_service = WorkflowService(self.workflow_store)

    def _load_plan(self, plan_id: str) -> TaskPlan:
        plan = self.plan_store.load(plan_id)
        if plan is None:
            raise ValueError(f"plan not found: {plan_id}")
        return plan

    def _load_plan_run(self, plan_run_id: str) -> PlanRun:
        plan_run = self.plan_run_store.load(plan_run_id)
        if plan_run is None:
            raise ValueError(f"plan_run not found: {plan_run_id}")
        return plan_run

    def _ensure_workflow_from_plan(self, workflow_id: str) -> None:
        if not workflow_id or self.workflow_store.load(workflow_id) is not None:
            return
        plan = self.plan_store.load(workflow_id)
        if plan is None:
            return
        status = WorkflowStatus.DRAFT
        plan_status = getattr(plan.status, "value", str(plan.status))
        if plan_status in {"running", "waiting_human", "completed", "failed"}:
            status = WorkflowStatus(plan_status)
        workflow = WorkflowRun(
            workflow_id=plan.plan_id,
            goal=plan.goal,
            context=plan.context,
            status=status,
            metadata={"imported_from_plan": True},
        )
        for step in plan.steps:
            step_status = getattr(step.status, "value", str(step.status))
            task_status = WorkflowTaskStatus(step_status) if step_status in WorkflowTaskStatus._value2member_map_ else WorkflowTaskStatus.PENDING
            workflow.tasks.append(
                WorkflowTask(
                    task_id=step.step_id,
                    title=step.title,
                    description=step.description,
                    dependencies=list(step.dependencies),
                    acceptance_criteria=list(step.acceptance_criteria),
                    suggested_tools=list(step.suggested_tools),
                    status=task_status,
                    result_summary=step.output,
                    error=step.error,
                    metadata={**step.metadata, "imported_from_plan_step": True},
                )
            )
        self.workflow_store.save(workflow)

    def _ensure_workflow_argument(self, arguments: dict[str, Any]) -> None:
        workflow_id = str(arguments.get("workflow_id") or arguments.get("plan_id") or "").strip()
        self._ensure_workflow_from_plan(workflow_id)

    def _task_to_dict(self, task: WorkflowTask, workflow_id: str) -> dict[str, Any]:
        data = task.to_dict()
        data["workflow_id"] = workflow_id
        data["plan_id"] = workflow_id
        return data

    def _workflow_to_plan_like_dict(self, workflow: WorkflowRun) -> dict[str, Any]:
        data = workflow.to_dict()
        data["plan_id"] = workflow.workflow_id
        data["steps"] = [self._task_to_dict(task, workflow.workflow_id) for task in workflow.tasks]
        return data


class TaskCreateTool(_TaskToolBase):
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ensure_workflow_argument(arguments)
        workflow, task, created_workflow = self.workflow_service.create_task(arguments)

        return {
            "ok": True,
            "tool_name": "task_create",
            "workflow_id": workflow.workflow_id,
            "plan_id": workflow.workflow_id,
            "task_id": task.task_id,
            "task": self._task_to_dict(task, workflow.workflow_id),
            "changed_files": [],
            "metadata": {"created_workflow": created_workflow, "created_plan": created_workflow},
        }


class TaskUpdateTool(_TaskToolBase):
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ensure_workflow_argument(arguments)
        workflow, task = self.workflow_service.update_task(arguments)

        plan_run_id = str(arguments.get("plan_run_id") or "").strip()
        if plan_run_id:
            self._sync_plan_run(plan_run_id, task)

        return {
            "ok": True,
            "tool_name": "task_update",
            "workflow_id": workflow.workflow_id,
            "plan_id": workflow.workflow_id,
            "task_id": task.task_id,
            "task": self._task_to_dict(task, workflow.workflow_id),
            "changed_files": [],
        }

    def _sync_plan_run(self, plan_run_id: str, task: WorkflowTask) -> None:
        plan_run = self._load_plan_run(plan_run_id)
        step_run = next((item for item in plan_run.step_runs if item.step_id == task.task_id), None)
        if step_run is None:
            step_run = StepRun(step_id=task.task_id, title=task.title)
            plan_run.step_runs.append(step_run)
        step_run.title = task.title
        step_run.status = _step_run_status(task.status)
        step_run.output = task.result_summary
        step_run.error = task.error
        step_run.metadata.setdefault("task_update_tool", True)
        self.plan_run_store.save(plan_run)


class TaskListTool(_TaskToolBase):
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        plan_run_id = str(arguments.get("plan_run_id") or "").strip()
        status_filter = str(arguments.get("status_filter") or "").strip().lower()
        limit = int(arguments.get("limit") or 50)

        if plan_run_id:
            plan_run = self._load_plan_run(plan_run_id)
            self._ensure_workflow_from_plan(plan_run.plan_id)
            pairs = self.workflow_service.list_tasks(workflow_id=plan_run.plan_id, status_filter=status_filter, limit=limit)
        else:
            workflow_id = str(arguments.get("workflow_id") or arguments.get("plan_id") or "").strip()
            self._ensure_workflow_from_plan(workflow_id)
            pairs = self.workflow_service.list_tasks(workflow_id=workflow_id, status_filter=status_filter, limit=limit)

        tasks = [self._task_to_dict(task, workflow.workflow_id) for workflow, task in pairs]

        return {
            "ok": True,
            "tool_name": "task_list",
            "tasks": tasks[:limit],
            "count": min(len(tasks), limit),
            "changed_files": [],
            "metadata": {"total_matched": len(tasks)},
        }


class TaskOutputTool(_TaskToolBase):
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        task_id = str(arguments.get("task_id") or "").strip()
        plan_run_id = str(arguments.get("plan_run_id") or "").strip()
        workflow_id = str(arguments.get("workflow_id") or arguments.get("plan_id") or "").strip()

        if task_id:
            self._ensure_workflow_from_plan(workflow_id)
            workflow, task = self.workflow_service.task_output(task_id, workflow_id=workflow_id)
            return {
                "ok": True,
                "tool_name": "task_output",
                "workflow_id": workflow.workflow_id,
                "plan_id": workflow.workflow_id,
                "task_id": task_id,
                "task": self._task_to_dict(task, workflow.workflow_id),
                "output": task.result_summary,
                "artifacts": [item.to_dict() for item in task.artifacts],
                "evidence": [item.to_dict() for item in task.evidence],
                "changed_files": [],
            }

        if plan_run_id:
            plan_run = self._load_plan_run(plan_run_id)
            return {
                "ok": True,
                "tool_name": "task_output",
                "plan_run_id": plan_run_id,
                "plan_run": plan_run.to_dict(),
                "output": plan_run.final_output,
                "changed_files": [],
            }

        if workflow_id:
            self._ensure_workflow_from_plan(workflow_id)
            workflow = self.workflow_service.load_workflow(workflow_id)
            return {
                "ok": True,
                "tool_name": "task_output",
                "workflow_id": workflow.workflow_id,
                "plan_id": workflow.workflow_id,
                "workflow": workflow.to_dict(),
                "plan": self._workflow_to_plan_like_dict(workflow),
                "tasks": [self._task_to_dict(task, workflow.workflow_id) for task in workflow.tasks],
                "changed_files": [],
            }

        raise ValueError("one of task_id, plan_id, or plan_run_id is required")


class TaskStopTool(_TaskToolBase):
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        reason = str(arguments.get("reason") or "stopped by task_stop")
        task_id = str(arguments.get("task_id") or "").strip()
        plan_run_id = str(arguments.get("plan_run_id") or "").strip()
        workflow_id = str(arguments.get("workflow_id") or arguments.get("plan_id") or "").strip()

        stopped: list[dict[str, str]] = []

        if task_id or workflow_id:
            self._ensure_workflow_from_plan(workflow_id)
            stopped.extend(self.workflow_service.stop(workflow_id=workflow_id, task_id=task_id, reason=reason))

        if plan_run_id:
            plan_run = self._load_plan_run(plan_run_id)
            plan_run.status = "cancelled"
            plan_run.completed = False
            plan_run.finished_at = time.time()
            plan_run.metadata["stop_reason"] = reason
            for step_run in plan_run.step_runs:
                if step_run.status in {StepRunStatus.PENDING, StepRunStatus.RUNNING, StepRunStatus.WAITING_HUMAN}:
                    step_run.status = StepRunStatus.SKIPPED
                    step_run.error = reason
            self.plan_run_store.save(plan_run)
            stopped.append({"plan_run_id": plan_run_id, "task_id": "*"})

        if not any([task_id, workflow_id, plan_run_id]):
            raise ValueError("one of task_id, workflow_id, plan_id, or plan_run_id is required")

        return {
            "ok": True,
            "tool_name": "task_stop",
            "stopped": stopped,
            "reason": reason,
            "changed_files": [],
        }


class TodoWriteTool(_TaskToolBase):
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        todos = arguments.get("todos")
        if not isinstance(todos, list):
            raise ValueError("todos must be a list")

        workflow_id = str(arguments.get("workflow_id") or arguments.get("plan_id") or "").strip()
        if workflow_id:
            workflow = self.workflow_service.load_workflow(workflow_id)
            created_workflow = False
        else:
            workflow = WorkflowRun(
                goal=str(arguments.get("goal") or "Todo list"),
                context=str(arguments.get("context") or ""),
            )
            created_workflow = True

        existing = {task.task_id: task for task in workflow.tasks}
        for index, raw_todo in enumerate(todos, 1):
            if not isinstance(raw_todo, dict):
                raise ValueError("each todo must be an object")
            task_id = str(raw_todo.get("id") or raw_todo.get("task_id") or f"todo_{index}").strip()
            content = str(raw_todo.get("content") or raw_todo.get("title") or task_id).strip()
            if not content:
                raise ValueError("todo content must be non-empty")
            status = _todo_status(str(raw_todo.get("status") or "pending"))
            task = existing.get(task_id)
            if task is None:
                task = WorkflowTask(
                    task_id=task_id,
                    title=content,
                    description=str(raw_todo.get("description") or ""),
                    status=status,
                    metadata={"source": "todo_write_tool"},
                )
                workflow.tasks.append(task)
                existing[task_id] = task
            else:
                task.title = content
                if "description" in raw_todo:
                    task.description = str(raw_todo.get("description") or "")
                task.status = status
                task.updated_at = time.time()
            if raw_todo.get("priority") is not None:
                task.metadata["priority"] = str(raw_todo["priority"])
            if raw_todo.get("linked_step_id") is not None:
                task.metadata["linked_step_id"] = str(raw_todo["linked_step_id"])

        workflow.updated_at = time.time()
        self.workflow_service._refresh_workflow_status(workflow)
        self.workflow_store.save(workflow)

        return {
            "ok": True,
            "tool_name": "todo_write",
            "workflow_id": workflow.workflow_id,
            "plan_id": workflow.workflow_id,
            "todos": [self._task_to_dict(task, workflow.workflow_id) for task in workflow.tasks],
            "count": len(workflow.tasks),
            "changed_files": [],
            "metadata": {"created_workflow": created_workflow},
        }
