from __future__ import annotations

import json
import re
from pathlib import Path

from .models import PlanRun


def _safe_id(value: str, label: str = "id") -> str:
    """Validate that an ID only contains safe filename characters."""
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", value):
        raise ValueError(f"Invalid {label}: {value!r} — only A-Za-z0-9_.- allowed")
    return value


class PlanRunStore:
    """将 PlanRun 持久化到磁盘，供审批后恢复执行。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, plan_run_id: str) -> Path:
        return self.root / f"{_safe_id(plan_run_id, 'plan_run_id')}.json"

    def save(self, plan_run: PlanRun) -> None:
        self._path(plan_run.plan_run_id).write_text(
            json.dumps(plan_run.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, plan_run_id: str) -> PlanRun | None:
        path = self._path(plan_run_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return PlanRun.from_dict(data)
