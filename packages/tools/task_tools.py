from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from packages.orchestrator.models import PlanRun, StepRun, StepRunStatus
from packages.orchestrator.store import PlanRunStore
from packages.planner.models import PlanStatus, StepStatus, TaskPlan, TaskStep
from packages.planner.store import PlanStore
from packages.runtime.config import PLAN_RUNS_DIR, PLANS_DIR


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _step_status(value: str) -> StepStatus:
    normalized = value.strip().lower()
    aliases = {
        "done": StepStatus.COMPLETED,
        "complete": StepStatus.COMPLETED,
        "completed": StepStatus.COMPLETED,
        "cancelled": StepStatus.SKIPPED,
        "canceled": StepStatus.SKIPPED,
        "blocked": StepStatus.SKIPPED,
    }
    if normalized in aliases:
        return aliases[normalized]
    return StepStatus(normalized)


def _step_run_status(status: StepStatus) -> StepRunStatus:
    mapping = {
        StepStatus.PENDING: StepRunStatus.PENDING,
        StepStatus.READY: StepRunStatus.PENDING,
        StepStatus.RUNNING: StepRunStatus.RUNNING,
        StepStatus.WAITING_HUMAN: StepRunStatus.WAITING_HUMAN,
        StepStatus.COMPLETED: StepRunStatus.COMPLETED,
        StepStatus.FAILED: StepRunStatus.FAILED,
        StepStatus.SKIPPED: StepRunStatus.SKIPPED,
    }
    return mapping[status]


class _TaskToolBase:
    def __init__(
        self,
        plan_store: PlanStore | None = None,
        plan_run_store: PlanRunStore | None = None,
        *,
        plans_dir: Path = PLANS_DIR,
        plan_runs_dir: Path = PLAN_RUNS_DIR,
    ) -> None:
        self.plan_store = plan_store or PlanStore(plans_dir)
        self.plan_run_store = plan_run_store or PlanRunStore(plan_runs_dir)

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

    def _find_step(self, plan: TaskPlan, task_id: str) -> TaskStep:
        for step in plan.steps:
            if step.step_id == task_id:
                return step
        raise ValueError(f"task not found: {task_id}")

    def _find_plan_containing_task(self, task_id: str) -> TaskPlan:
        for plan in reversed(self.plan_store.list_recent(limit=200)):
            if any(step.step_id == task_id for step in plan.steps):
                return plan
        raise ValueError(f"task not found: {task_id}")

    def _step_to_dict(self, step: TaskStep, plan_id: str) -> dict[str, Any]:
        data = step.to_dict()
        data["task_id"] = step.step_id
        data["plan_id"] = plan_id
        return data


class TaskCreateTool(_TaskToolBase):
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(arguments.get("plan_id") or "").strip()
        if plan_id:
            plan = self._load_plan(plan_id)
        else:
            plan = TaskPlan(
                goal=str(arguments.get("goal") or "Ad-hoc task plan"),
                context=str(arguments.get("context") or ""),
            )

        task_id = str(arguments.get("task_id") or f"task_{uuid.uuid4().hex[:8]}")
        if any(step.step_id == task_id for step in plan.steps):
            raise ValueError(f"task already exists: {task_id}")

        step = TaskStep(
            step_id=task_id,
            title=str(arguments.get("title") or task_id),
            description=str(arguments.get("description") or ""),
            dependencies=_as_list(arguments.get("dependencies")),
            acceptance_criteria=_as_list(arguments.get("acceptance_criteria")),
            suggested_tools=_as_list(arguments.get("suggested_tools")),
            metadata={
                "source": "task_create_tool",
                "target_files": _as_list(arguments.get("target_files")),
                "artifacts": arguments.get("artifacts") if isinstance(arguments.get("artifacts"), list) else [],
                "evidence": arguments.get("evidence") if isinstance(arguments.get("evidence"), list) else [],
            },
        )
        plan.steps.append(step)
        plan.updated_at = time.time()
        self.plan_store.save(plan)

        return {
            "ok": True,
            "tool_name": "task_create",
            "plan_id": plan.plan_id,
            "task_id": step.step_id,
            "task": self._step_to_dict(step, plan.plan_id),
            "changed_files": [],
            "metadata": {"created_plan": not bool(plan_id)},
        }


class TaskUpdateTool(_TaskToolBase):
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        task_id = str(arguments.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")

        plan_id = str(arguments.get("plan_id") or "").strip()
        plan = self._load_plan(plan_id) if plan_id else self._find_plan_containing_task(task_id)
        step = self._find_step(plan, task_id)

        if "status" in arguments and arguments["status"] is not None:
            new_status = _step_status(str(arguments["status"]))
            self._validate_transition(step.status, new_status)
            step.status = new_status
        if "title" in arguments and arguments["title"] is not None:
            step.title = str(arguments["title"])
        if "description" in arguments and arguments["description"] is not None:
            step.description = str(arguments["description"])
        if "result_summary" in arguments and arguments["result_summary"] is not None:
            step.output = str(arguments["result_summary"])
        if "error" in arguments and arguments["error"] is not None:
            step.error = str(arguments["error"])
        if "acceptance_criteria" in arguments:
            step.acceptance_criteria = _as_list(arguments.get("acceptance_criteria"))
        if "dependencies" in arguments:
            step.dependencies = _as_list(arguments.get("dependencies"))
        if "target_files" in arguments:
            step.metadata["target_files"] = _as_list(arguments.get("target_files"))
        if isinstance(arguments.get("artifacts"), list):
            step.metadata["artifacts"] = arguments["artifacts"]
        if isinstance(arguments.get("evidence"), list):
            step.metadata["evidence"] = arguments["evidence"]

        plan.updated_at = time.time()
        self.plan_store.save(plan)

        plan_run_id = str(arguments.get("plan_run_id") or "").strip()
        if plan_run_id:
            self._sync_plan_run(plan_run_id, step)

        return {
            "ok": True,
            "tool_name": "task_update",
            "plan_id": plan.plan_id,
            "task_id": step.step_id,
            "task": self._step_to_dict(step, plan.plan_id),
            "changed_files": [],
        }

    def _validate_transition(self, old: StepStatus, new: StepStatus) -> None:
        if old in {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED} and new == StepStatus.RUNNING:
            raise ValueError(f"invalid task status transition: {old.value} -> {new.value}")

    def _sync_plan_run(self, plan_run_id: str, step: TaskStep) -> None:
        plan_run = self._load_plan_run(plan_run_id)
        step_run = next((item for item in plan_run.step_runs if item.step_id == step.step_id), None)
        if step_run is None:
            step_run = StepRun(step_id=step.step_id, title=step.title)
            plan_run.step_runs.append(step_run)
        step_run.title = step.title
        step_run.status = _step_run_status(step.status)
        step_run.output = step.output
        step_run.error = step.error
        step_run.metadata.setdefault("task_update_tool", True)
        self.plan_run_store.save(plan_run)


class TaskListTool(_TaskToolBase):
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        plan_run_id = str(arguments.get("plan_run_id") or "").strip()
        status_filter = str(arguments.get("status_filter") or "").strip().lower()
        limit = int(arguments.get("limit") or 50)

        if plan_run_id:
            plan_run = self._load_plan_run(plan_run_id)
            plan = self._load_plan(plan_run.plan_id)
            tasks = [self._step_to_dict(step, plan.plan_id) for step in plan.steps]
        else:
            plan_id = str(arguments.get("plan_id") or "").strip()
            plans = [self._load_plan(plan_id)] if plan_id else self.plan_store.list_recent(limit=limit)
            tasks = []
            for plan in plans:
                tasks.extend(self._step_to_dict(step, plan.plan_id) for step in plan.steps)

        if status_filter:
            tasks = [task for task in tasks if str(task.get("status")) == status_filter]

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
        plan_id = str(arguments.get("plan_id") or "").strip()

        if task_id:
            plan = self._load_plan(plan_id) if plan_id else self._find_plan_containing_task(task_id)
            step = self._find_step(plan, task_id)
            return {
                "ok": True,
                "tool_name": "task_output",
                "plan_id": plan.plan_id,
                "task_id": task_id,
                "task": self._step_to_dict(step, plan.plan_id),
                "output": step.output,
                "artifacts": step.metadata.get("artifacts", []),
                "evidence": step.metadata.get("evidence", []),
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

        if plan_id:
            plan = self._load_plan(plan_id)
            return {
                "ok": True,
                "tool_name": "task_output",
                "plan_id": plan_id,
                "plan": plan.to_dict(),
                "tasks": [self._step_to_dict(step, plan.plan_id) for step in plan.steps],
                "changed_files": [],
            }

        raise ValueError("one of task_id, plan_id, or plan_run_id is required")


class TaskStopTool(_TaskToolBase):
    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        reason = str(arguments.get("reason") or "stopped by task_stop")
        task_id = str(arguments.get("task_id") or "").strip()
        plan_run_id = str(arguments.get("plan_run_id") or "").strip()
        plan_id = str(arguments.get("plan_id") or "").strip()

        stopped: list[dict[str, str]] = []

        if task_id:
            plan = self._load_plan(plan_id) if plan_id else self._find_plan_containing_task(task_id)
            step = self._find_step(plan, task_id)
            step.status = StepStatus.SKIPPED
            step.error = reason
            step.metadata["stopped"] = True
            step.metadata["stop_reason"] = reason
            plan.updated_at = time.time()
            self.plan_store.save(plan)
            stopped.append({"plan_id": plan.plan_id, "task_id": task_id})

        if plan_id and not task_id:
            plan = self._load_plan(plan_id)
            for step in plan.steps:
                if step.status in {StepStatus.PENDING, StepStatus.READY, StepStatus.RUNNING, StepStatus.WAITING_HUMAN}:
                    step.status = StepStatus.SKIPPED
                    step.error = reason
                    step.metadata["stopped"] = True
                    step.metadata["stop_reason"] = reason
                    stopped.append({"plan_id": plan.plan_id, "task_id": step.step_id})
            plan.status = PlanStatus.FAILED
            plan.updated_at = time.time()
            self.plan_store.save(plan)

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

        if not any([task_id, plan_id, plan_run_id]):
            raise ValueError("one of task_id, plan_id, or plan_run_id is required")

        return {
            "ok": True,
            "tool_name": "task_stop",
            "stopped": stopped,
            "reason": reason,
            "changed_files": [],
        }
