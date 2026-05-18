from __future__ import annotations

import time
import uuid
from typing import Any

from .models import Artifact, Evidence, WorkflowRun, WorkflowStatus, WorkflowTask, WorkflowTaskStatus
from .store import WorkflowStore


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def normalize_task_status(value: str) -> WorkflowTaskStatus:
    normalized = value.strip().lower()
    aliases = {
        "done": WorkflowTaskStatus.COMPLETED,
        "complete": WorkflowTaskStatus.COMPLETED,
        "completed": WorkflowTaskStatus.COMPLETED,
        "cancelled": WorkflowTaskStatus.CANCELLED,
        "canceled": WorkflowTaskStatus.CANCELLED,
        "blocked": WorkflowTaskStatus.BLOCKED,
    }
    if normalized in aliases:
        return aliases[normalized]
    return WorkflowTaskStatus(normalized)


class WorkflowService:
    def __init__(self, store: WorkflowStore) -> None:
        self.store = store

    def load_workflow(self, workflow_id: str) -> WorkflowRun:
        workflow = self.store.load(workflow_id)
        if workflow is None:
            raise ValueError(f"workflow not found: {workflow_id}")
        return workflow

    def create_task(self, arguments: dict[str, Any]) -> tuple[WorkflowRun, WorkflowTask, bool]:
        workflow_id = str(arguments.get("workflow_id") or arguments.get("plan_id") or "").strip()
        created_workflow = False
        if workflow_id:
            workflow = self.load_workflow(workflow_id)
        else:
            workflow = WorkflowRun(
                goal=str(arguments.get("goal") or "Ad-hoc workflow"),
                context=str(arguments.get("context") or ""),
                status=WorkflowStatus.DRAFT,
            )
            created_workflow = True

        task_id = str(arguments.get("task_id") or f"task_{uuid.uuid4().hex[:8]}")
        if any(task.task_id == task_id for task in workflow.tasks):
            raise ValueError(f"task already exists: {task_id}")
        dependencies = as_list(arguments.get("dependencies"))
        self.validate_dependencies(workflow, dependencies, task_id=task_id)

        task = WorkflowTask(
            task_id=task_id,
            title=str(arguments.get("title") or task_id),
            description=str(arguments.get("description") or ""),
            dependencies=dependencies,
            acceptance_criteria=as_list(arguments.get("acceptance_criteria")),
            suggested_tools=as_list(arguments.get("suggested_tools")),
            target_files=as_list(arguments.get("target_files")),
            evidence=[Evidence.from_dict(item) for item in arguments.get("evidence", []) if isinstance(item, dict)],
            artifacts=[Artifact.from_dict(item) for item in arguments.get("artifacts", []) if isinstance(item, dict)],
            metadata={"source": "task_create_tool"},
        )
        workflow.tasks.append(task)
        workflow.updated_at = time.time()
        self.store.save(workflow)
        return workflow, task, created_workflow

    def update_task(self, arguments: dict[str, Any]) -> tuple[WorkflowRun, WorkflowTask]:
        task_id = str(arguments.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        workflow_id = str(arguments.get("workflow_id") or arguments.get("plan_id") or "").strip()
        workflow = self.load_workflow(workflow_id) if workflow_id else self.find_workflow_containing_task(task_id)
        task = self.find_task(workflow, task_id)

        if "status" in arguments and arguments["status"] is not None:
            new_status = normalize_task_status(str(arguments["status"]))
            self.validate_transition(task.status, new_status)
            task.status = new_status
            if new_status == WorkflowTaskStatus.RUNNING:
                task.attempts.append(TaskAttemptFactory.create(task.task_id))
        if "title" in arguments and arguments["title"] is not None:
            task.title = str(arguments["title"])
        if "description" in arguments and arguments["description"] is not None:
            task.description = str(arguments["description"])
        if "result_summary" in arguments and arguments["result_summary"] is not None:
            task.result_summary = str(arguments["result_summary"])
        if "error" in arguments and arguments["error"] is not None:
            task.error = str(arguments["error"])
        if "acceptance_criteria" in arguments:
            task.acceptance_criteria = as_list(arguments.get("acceptance_criteria"))
        if "dependencies" in arguments:
            dependencies = as_list(arguments.get("dependencies"))
            self.validate_dependencies(workflow, dependencies, task_id=task_id)
            task.dependencies = dependencies
        if "target_files" in arguments:
            task.target_files = as_list(arguments.get("target_files"))
        if isinstance(arguments.get("artifacts"), list):
            task.artifacts = [Artifact.from_dict(item) for item in arguments["artifacts"] if isinstance(item, dict)]
        if isinstance(arguments.get("evidence"), list):
            task.evidence = [Evidence.from_dict(item) for item in arguments["evidence"] if isinstance(item, dict)]

        task.updated_at = time.time()
        workflow.updated_at = time.time()
        self._refresh_workflow_status(workflow)
        self.store.save(workflow)
        return workflow, task

    def list_tasks(
        self,
        *,
        workflow_id: str = "",
        status_filter: str = "",
        limit: int = 50,
    ) -> list[tuple[WorkflowRun, WorkflowTask]]:
        workflows = [self.load_workflow(workflow_id)] if workflow_id else self.store.list_recent(limit=limit)
        pairs: list[tuple[WorkflowRun, WorkflowTask]] = []
        for workflow in workflows:
            for task in workflow.tasks:
                if status_filter and task.status.value != status_filter:
                    continue
                pairs.append((workflow, task))
        return pairs[:limit]

    def task_output(self, task_id: str, workflow_id: str = "") -> tuple[WorkflowRun, WorkflowTask]:
        workflow = self.load_workflow(workflow_id) if workflow_id else self.find_workflow_containing_task(task_id)
        return workflow, self.find_task(workflow, task_id)

    def stop(self, *, workflow_id: str = "", task_id: str = "", reason: str) -> list[dict[str, str]]:
        stopped: list[dict[str, str]] = []
        if task_id:
            workflow = self.load_workflow(workflow_id) if workflow_id else self.find_workflow_containing_task(task_id)
            task = self.find_task(workflow, task_id)
            task.status = WorkflowTaskStatus.CANCELLED
            task.error = reason
            task.metadata["stopped"] = True
            task.metadata["stop_reason"] = reason
            task.updated_at = time.time()
            workflow.updated_at = time.time()
            self._refresh_workflow_status(workflow)
            self.store.save(workflow)
            return [{"workflow_id": workflow.workflow_id, "task_id": task_id}]

        if workflow_id:
            workflow = self.load_workflow(workflow_id)
            for task in workflow.tasks:
                if task.status in {
                    WorkflowTaskStatus.PENDING,
                    WorkflowTaskStatus.READY,
                    WorkflowTaskStatus.RUNNING,
                    WorkflowTaskStatus.WAITING_HUMAN,
                    WorkflowTaskStatus.BLOCKED,
                }:
                    task.status = WorkflowTaskStatus.CANCELLED
                    task.error = reason
                    task.metadata["stopped"] = True
                    task.metadata["stop_reason"] = reason
                    task.updated_at = time.time()
                    stopped.append({"workflow_id": workflow.workflow_id, "task_id": task.task_id})
            workflow.status = WorkflowStatus.CANCELLED
            workflow.updated_at = time.time()
            self.store.save(workflow)
        return stopped

    def find_workflow_containing_task(self, task_id: str) -> WorkflowRun:
        matches = self.store.find_containing_task(task_id, limit=200)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            workflow_ids = ", ".join(item.workflow_id for item in matches[:5])
            raise ValueError(f"task_id is ambiguous; provide workflow_id. matches: {workflow_ids}")
        raise ValueError(f"task not found: {task_id}")

    def find_task(self, workflow: WorkflowRun, task_id: str) -> WorkflowTask:
        for task in workflow.tasks:
            if task.task_id == task_id:
                return task
        raise ValueError(f"task not found: {task_id}")

    def validate_dependencies(self, workflow: WorkflowRun, dependencies: list[str], *, task_id: str | None = None) -> None:
        existing = {task.task_id for task in workflow.tasks}
        missing = [dep for dep in dependencies if dep not in existing]
        if missing:
            raise ValueError(f"dependencies not found in workflow: {', '.join(missing)}")
        if task_id and task_id in dependencies:
            raise ValueError("task cannot depend on itself")

    def validate_transition(self, old: WorkflowTaskStatus, new: WorkflowTaskStatus) -> None:
        if old == new:
            return
        allowed = {
            WorkflowTaskStatus.PENDING: {WorkflowTaskStatus.READY, WorkflowTaskStatus.RUNNING, WorkflowTaskStatus.WAITING_HUMAN, WorkflowTaskStatus.COMPLETED, WorkflowTaskStatus.FAILED, WorkflowTaskStatus.SKIPPED, WorkflowTaskStatus.BLOCKED, WorkflowTaskStatus.CANCELLED},
            WorkflowTaskStatus.READY: {WorkflowTaskStatus.RUNNING, WorkflowTaskStatus.WAITING_HUMAN, WorkflowTaskStatus.COMPLETED, WorkflowTaskStatus.FAILED, WorkflowTaskStatus.SKIPPED, WorkflowTaskStatus.BLOCKED, WorkflowTaskStatus.CANCELLED},
            WorkflowTaskStatus.RUNNING: {WorkflowTaskStatus.WAITING_HUMAN, WorkflowTaskStatus.COMPLETED, WorkflowTaskStatus.FAILED, WorkflowTaskStatus.SKIPPED, WorkflowTaskStatus.BLOCKED, WorkflowTaskStatus.CANCELLED},
            WorkflowTaskStatus.WAITING_HUMAN: {WorkflowTaskStatus.RUNNING, WorkflowTaskStatus.COMPLETED, WorkflowTaskStatus.FAILED, WorkflowTaskStatus.SKIPPED, WorkflowTaskStatus.BLOCKED, WorkflowTaskStatus.CANCELLED},
            WorkflowTaskStatus.FAILED: {WorkflowTaskStatus.PENDING, WorkflowTaskStatus.SKIPPED, WorkflowTaskStatus.CANCELLED},
            WorkflowTaskStatus.BLOCKED: {WorkflowTaskStatus.PENDING, WorkflowTaskStatus.RUNNING, WorkflowTaskStatus.SKIPPED, WorkflowTaskStatus.CANCELLED},
            WorkflowTaskStatus.COMPLETED: set(),
            WorkflowTaskStatus.SKIPPED: set(),
            WorkflowTaskStatus.CANCELLED: set(),
        }
        if new not in allowed.get(old, set()):
            raise ValueError(f"invalid task status transition: {old.value} -> {new.value}")

    def _refresh_workflow_status(self, workflow: WorkflowRun) -> None:
        statuses = {task.status for task in workflow.tasks}
        if not statuses:
            workflow.status = WorkflowStatus.DRAFT
        elif WorkflowTaskStatus.WAITING_HUMAN in statuses:
            workflow.status = WorkflowStatus.WAITING_HUMAN
        elif WorkflowTaskStatus.RUNNING in statuses:
            workflow.status = WorkflowStatus.RUNNING
        elif any(status in statuses for status in {WorkflowTaskStatus.FAILED, WorkflowTaskStatus.BLOCKED}):
            workflow.status = WorkflowStatus.FAILED
        elif all(status in {WorkflowTaskStatus.COMPLETED, WorkflowTaskStatus.SKIPPED, WorkflowTaskStatus.CANCELLED} for status in statuses):
            workflow.status = WorkflowStatus.COMPLETED
        else:
            workflow.status = WorkflowStatus.DRAFT


class TaskAttemptFactory:
    @staticmethod
    def create(task_id: str):
        from .models import TaskAttempt

        return TaskAttempt(task_id=task_id, status=WorkflowTaskStatus.RUNNING)
