from __future__ import annotations

import json
import re
from pathlib import Path

from .models import WorkflowRun


def _safe_id(value: str, label: str = "id") -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", value):
        raise ValueError(f"Invalid {label}: {value!r} — only A-Za-z0-9_.- allowed")
    return value


class WorkflowStore:
    """Persist WorkflowRun objects as one JSON file per workflow."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, workflow_id: str) -> Path:
        return self.root / f"{_safe_id(workflow_id, 'workflow_id')}.json"

    def save(self, workflow: WorkflowRun) -> None:
        self._path(workflow.workflow_id).write_text(
            json.dumps(workflow.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, workflow_id: str) -> WorkflowRun | None:
        path = self._path(workflow_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return WorkflowRun.from_dict(data)

    def list_all(self) -> list[WorkflowRun]:
        workflows: list[WorkflowRun] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                workflows.append(WorkflowRun.from_dict(data))
            except Exception:
                pass
        workflows.sort(key=lambda item: item.created_at)
        return workflows

    def list_recent(self, limit: int = 20) -> list[WorkflowRun]:
        workflows = self.list_all()
        workflows.sort(key=lambda item: item.updated_at or item.created_at, reverse=True)
        return workflows[:limit]

    def find_containing_task(self, task_id: str, limit: int = 200) -> list[WorkflowRun]:
        return [
            workflow
            for workflow in self.list_recent(limit=limit)
            if any(task.task_id == task_id for task in workflow.tasks)
        ]
