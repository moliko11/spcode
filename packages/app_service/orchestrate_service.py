"""
OrchestrateService — 封装 Orchestrator 的启动/审批/恢复操作。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from packages.orchestrator.orchestrator import Orchestrator
from packages.orchestrator.store import PlanRunStore
from packages.planner.planner import Planner
from packages.planner.store import PlanStore
from packages.runtime.bootstrap import build_runtime, build_llm
from packages.runtime.config import PLANS_DIR, PLAN_RUNS_DIR


@dataclass
class PlanRunSummary:
    """plan run 对外摘要（不暴露内部数据类）"""
    plan_run_id: str
    status: str
    goal: str
    total_steps: int
    completed_steps: int
    failed_steps: int
    waiting_step: dict[str, Any] | None  # None 表示无等待
    cost_summary: dict[str, Any]
    raw: dict[str, Any]  # 完整 to_dict() 供 --json 输出

    @property
    def waiting_human(self) -> bool:
        return self.status == "waiting_human"

    @property
    def done(self) -> bool:
        return self.status in ("completed", "failed")


def _plan_run_to_summary(plan_run: Any) -> PlanRunSummary:
    d = plan_run.to_dict() if hasattr(plan_run, "to_dict") else {}
    step_runs = getattr(plan_run, "step_runs", []) or []
    completed = sum(1 for s in step_runs if getattr(s, "status", None) and s.status.value == "completed")
    failed = sum(1 for s in step_runs if getattr(s, "status", None) and s.status.value == "failed")
    waiting = next(
        (s for s in step_runs if getattr(s, "status", None) and s.status.value == "waiting_human"),
        None,
    )
    waiting_dict: dict[str, Any] | None = None
    if waiting is not None:
        pending_req = getattr(waiting, "pending_human_request", None) or {}
        waiting_dict = {
            "step_run_id": getattr(waiting, "step_run_id", ""),
            "step_id": getattr(waiting, "step_id", ""),
            "pending_human_request": pending_req,
        }

    # 累加 cost
    from packages.runtime.cost import CostTracker, TokenUsage
    tracker = CostTracker()
    for sr in step_runs:
        sr_meta = getattr(sr, "metadata", {}) or {}
        rt_cost = sr_meta.get("runtime_cost_summary") if isinstance(sr_meta, dict) else None
        if isinstance(rt_cost, dict) and rt_cost.get("total_tokens", 0) > 0:
            model_name = rt_cost.get("model_name", "unknown")
            usage = TokenUsage(
                input_tokens=rt_cost.get("input_tokens", 0),
                output_tokens=rt_cost.get("output_tokens", 0),
                total_tokens=rt_cost.get("total_tokens", 0),
            )
            tracker.add(model_name, usage)
    cost_summary = tracker.get_summary() if hasattr(tracker, "get_summary") else {}

    return PlanRunSummary(
        plan_run_id=plan_run.plan_run_id,
        status=plan_run.status if isinstance(plan_run.status, str) else str(plan_run.status),
        goal=getattr(plan_run, "goal", ""),
        total_steps=len(step_runs),
        completed_steps=completed,
        failed_steps=failed,
        waiting_step=waiting_dict,
        cost_summary=cost_summary,
        raw=d,
    )


class OrchestrateService:
    """
    Orchestrate 业务服务。

    usage::

        svc = OrchestrateService.from_env()
        summary = await svc.run(goal="...", user_id="demo")
        if summary.waiting_human:
            summary = await svc.approve(summary.plan_run_id, approved=True)
    """

    def __init__(self, orchestrator: Orchestrator, plan_run_store: PlanRunStore) -> None:
        self._orchestrator = orchestrator
        self._plan_run_store = plan_run_store

    @classmethod
    def from_env(
        cls,
        provider: str | None = None,
        user_id: str = "demo-user",
        session_id: str | None = None,
    ) -> "OrchestrateService":
        import os
        if provider:
            os.environ["MOLIKO_LLM_PROVIDER"] = provider
        runtime = build_runtime()
        llm = build_llm()
        planner = Planner(llm=llm)
        plan_store = PlanStore(PLANS_DIR)
        plan_run_store = PlanRunStore(PLAN_RUNS_DIR)
        orchestrator = Orchestrator(
            runtime=runtime,
            planner=planner,
            plan_store=plan_store,
            plan_run_store=plan_run_store,
            user_id=user_id,
            **({"session_id": session_id} if session_id else {}),
        )
        return cls(orchestrator=orchestrator, plan_run_store=plan_run_store)

    async def run(self, goal: str, context: str = "") -> PlanRunSummary:
        """启动一次完整 orchestrate，返回摘要。"""
        plan_run = await self._orchestrator.run(goal=goal, context=context)
        return _plan_run_to_summary(plan_run)

    async def approve(
        self,
        plan_run_id: str,
        approved: bool,
        approved_by: str = "human",
        edited_arguments: dict[str, Any] | None = None,
    ) -> PlanRunSummary:
        """对 waiting_human 的 plan run 做审批并继续。"""
        plan_run = await self._orchestrator.resume(
            plan_run_id=plan_run_id,
            approved=approved,
            approved_by=approved_by,
            edited_arguments=edited_arguments,
        )
        return _plan_run_to_summary(plan_run)

    async def recover(self, plan_run_id: str) -> PlanRunSummary:
        """跨进程恢复崩溃/中断的 plan run。"""
        plan_run = self._plan_run_store.load(plan_run_id)
        if plan_run is None:
            raise ValueError(f"plan_run not found: {plan_run_id}")
        if plan_run.status in ("completed", "failed"):
            return _plan_run_to_summary(plan_run)
        plan_run = await self._orchestrator.recover(plan_run_id)
        return _plan_run_to_summary(plan_run)

    def get_plan_run(self, plan_run_id: str) -> PlanRunSummary | None:
        plan_run = self._plan_run_store.load(plan_run_id)
        if plan_run is None:
            return None
        return _plan_run_to_summary(plan_run)

    def list_plan_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近的 plan run（基础信息）。"""
        plan_runs = self._plan_run_store.list_recent(limit=limit)
        return [
            {
                "plan_run_id": pr.plan_run_id,
                "goal": getattr(pr, "goal", ""),
                "status": pr.status if isinstance(pr.status, str) else str(pr.status),
                "steps": len(getattr(pr, "step_runs", []) or []),
            }
            for pr in plan_runs
        ]
