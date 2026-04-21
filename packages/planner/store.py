from __future__ import annotations

import json
from pathlib import Path

from .models import TaskPlan


class PlanStore:
    """
    将 TaskPlan 持久化到磁盘（每个 plan 一个 JSON 文件）。

    目录结构：
        <root>/
            <plan_id>.json
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, plan_id: str) -> Path:
        return self.root / f"{plan_id}.json"

    def save(self, plan: TaskPlan) -> None:
        """保存或覆盖一个 plan"""
        self._path(plan.plan_id).write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, plan_id: str) -> TaskPlan | None:
        """按 plan_id 加载；不存在返回 None"""
        path = self._path(plan_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return TaskPlan.from_dict(data)

    def list_all(self) -> list[TaskPlan]:
        """返回所有已保存的 plan（按创建时间升序）"""
        plans: list[TaskPlan] = []
        for p in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                plans.append(TaskPlan.from_dict(data))
            except Exception:
                pass
        plans.sort(key=lambda pl: pl.created_at)
        return plans
