"""
PlanService — 封装 Planner.create_plan()，供 CLI/API 调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packages.planner.planner import Planner
from packages.planner.models import TaskPlan
from packages.planner.store import PlanStore
from packages.runtime.bootstrap import build_llm
from packages.runtime.config import PLANS_DIR


@dataclass
class PlanResult:
    """create_plan 返回的对外结果"""
    plan_id: str
    goal: str
    steps: list[dict[str, Any]]
    status: str
    token_usage: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "steps": self.steps,
            "status": self.status,
            "token_usage": self.token_usage,
        }


def _plan_to_result(plan: TaskPlan, token_usage: dict[str, Any]) -> PlanResult:
    return PlanResult(
        plan_id=plan.plan_id,
        goal=plan.goal,
        steps=[s.to_dict() if hasattr(s, "to_dict") else vars(s) for s in plan.steps],
        status=plan.status.value if hasattr(plan.status, "value") else str(plan.status),
        token_usage=token_usage,
    )


class PlanService:
    """
    Plan 业务服务。

    usage::

        svc = PlanService.from_env()
        result = await svc.create_plan(goal="重构 runtime 模块", context="...")
        print(result.plan_id)
    """

    def __init__(self, planner: Planner, store: PlanStore) -> None:
        self._planner = planner
        self._store = store

    @classmethod
    def from_env(cls, provider: str | None = None) -> "PlanService":
        import os
        if provider:
            os.environ["MOLIKO_LLM_PROVIDER"] = provider
        llm = build_llm()
        planner = Planner(llm=llm)
        store = PlanStore(PLANS_DIR)
        return cls(planner=planner, store=store)

    async def create_plan(self, goal: str, context: str = "") -> PlanResult:
        """生成计划，自动持久化，返回对外结果。"""
        plan = await self._planner.create_plan(goal=goal, context=context)
        self._store.save(plan)

        token_usage: dict[str, Any] = {}
        if self._planner.last_token_usage and self._planner.last_token_usage.total_tokens > 0:
            u = self._planner.last_token_usage
            token_usage = {
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "total_tokens": u.total_tokens,
            }

        return _plan_to_result(plan, token_usage)

    def get_plan(self, plan_id: str) -> PlanResult | None:
        plan = self._store.load(plan_id)
        if plan is None:
            return None
        return _plan_to_result(plan, {})

    def list_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近的计划（基础信息）。"""
        plans = self._store.list_recent(limit=limit)
        return [
            {
                "plan_id": p.plan_id,
                "goal": p.goal,
                "steps": len(p.steps),
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
            }
            for p in plans
        ]
