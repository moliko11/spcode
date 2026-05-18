from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .models import WorkflowRun, WorkflowTask, WorkflowTaskStatus
from .service import WorkflowService, as_list


@dataclass
class ReplanResult:
    workflow_id: str
    strategy: str
    failed_task_id: str
    added_task_ids: list[str] = field(default_factory=list)
    replaced_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "strategy": self.strategy,
            "failed_task_id": self.failed_task_id,
            "added_task_ids": self.added_task_ids,
            "replaced_task_id": self.replaced_task_id,
            "metadata": self.metadata,
        }


class Replanner:
    def __init__(self, service: WorkflowService) -> None:
        self.service = service

    def replan_failed_task(
        self,
        *,
        workflow_id: str,
        failed_task_id: str,
        new_tasks: list[dict[str, Any]],
        strategy: str = "append",
        reason: str = "",
    ) -> ReplanResult:
        if strategy not in {"append", "replace"}:
            raise ValueError("strategy must be append or replace")
        workflow = self.service.load_workflow(workflow_id)
        failed_task = self.service.find_task(workflow, failed_task_id)
        if failed_task.status != WorkflowTaskStatus.FAILED:
            raise ValueError("failed_task_id must point to a failed task")
        if not new_tasks:
            raise ValueError("new_tasks must not be empty")

        added: list[str] = []
        if strategy == "replace":
            failed_task.status = WorkflowTaskStatus.SKIPPED
            failed_task.metadata["replaced_by_replan"] = True
            failed_task.metadata["replan_reason"] = reason

        for index, raw_task in enumerate(new_tasks, 1):
            task_id = str(raw_task.get("task_id") or f"replan_{uuid.uuid4().hex[:8]}")
            if any(task.task_id == task_id for task in workflow.tasks):
                raise ValueError(f"task already exists: {task_id}")
            dependencies = as_list(raw_task.get("dependencies"))
            if strategy == "append" and not dependencies:
                dependencies = [failed_task_id]
            self.service.validate_dependencies(workflow, dependencies, task_id=task_id)
            task = WorkflowTask(
                task_id=task_id,
                title=str(raw_task.get("title") or f"Replan task {index}"),
                description=str(raw_task.get("description") or ""),
                dependencies=dependencies,
                acceptance_criteria=as_list(raw_task.get("acceptance_criteria")),
                suggested_tools=as_list(raw_task.get("suggested_tools")),
                target_files=as_list(raw_task.get("target_files")),
                metadata={"source": "replanner", "replans": failed_task_id, "reason": reason},
            )
            workflow.tasks.append(task)
            added.append(task.task_id)

        workflow.metadata.setdefault("replans", []).append(
            {
                "failed_task_id": failed_task_id,
                "strategy": strategy,
                "added_task_ids": added,
                "reason": reason,
                "created_at": time.time(),
            }
        )
        workflow.updated_at = time.time()
        self.service._refresh_workflow_status(workflow)
        self.service.store.save(workflow)
        return ReplanResult(
            workflow_id=workflow.workflow_id,
            strategy=strategy,
            failed_task_id=failed_task_id,
            added_task_ids=added,
            replaced_task_id=failed_task_id if strategy == "replace" else None,
            metadata={"reason": reason},
        )
