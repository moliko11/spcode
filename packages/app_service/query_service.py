"""
QueryService — 只读查询：session 消息 / 记忆 / run / plan_run 列表。

不做状态变更，CLI 的 list/show/watch 命令和 Web GET 端点都走这里。
"""

from __future__ import annotations

from typing import Any

from packages.runtime.store import FileSessionStore, FileCheckpointStore
from packages.runtime.config import SESSION_DIR, CHECKPOINT_DIR, MEMORY_USERS_DIR, PLAN_RUNS_DIR, PLANS_DIR
from packages.memory.store import FileMemoryStore
from packages.planner.store import PlanStore
from packages.orchestrator.store import PlanRunStore


class QueryService:
    """
    只读查询服务。

    usage::

        qs = QueryService.from_env()
        messages = await qs.get_session_messages("session-id")
        memories  = await qs.list_memories("demo-user", limit=10)
        runs      = qs.list_plan_runs(limit=20)
    """

    def __init__(
        self,
        session_store: FileSessionStore,
        memory_store: FileMemoryStore,
        plan_store: PlanStore,
        plan_run_store: PlanRunStore,
        checkpoint_store: FileCheckpointStore,
    ) -> None:
        self._session_store = session_store
        self._memory_store = memory_store
        self._plan_store = plan_store
        self._plan_run_store = plan_run_store
        self._checkpoint_store = checkpoint_store

    @classmethod
    def from_env(cls) -> "QueryService":
        return cls(
            session_store=FileSessionStore(SESSION_DIR),
            memory_store=FileMemoryStore(MEMORY_USERS_DIR),
            plan_store=PlanStore(PLANS_DIR),
            plan_run_store=PlanRunStore(PLAN_RUNS_DIR),
            checkpoint_store=FileCheckpointStore(CHECKPOINT_DIR),
        )

    # ── session ───────────────────────────────────────────────────────────

    async def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        messages = await self._session_store.load_messages(session_id)
        return [
            {"role": m.role, "content": m.content, "created_at": m.created_at}
            for m in messages
        ]

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出已有 session（基础信息）。"""
        sessions = self._session_store.list_sessions(limit=limit)
        return [
            {"session_id": s.session_id, "updated_at": getattr(s, "updated_at", None)}
            for s in sessions
        ] if sessions else []

    # ── memory ────────────────────────────────────────────────────────────

    async def list_memories(
        self,
        user_id: str,
        limit: int = 20,
        memory_type: str | None = None,
    ) -> list[dict[str, Any]]:
        entries = await self._memory_store.list_recent(user_id, limit=limit)
        if memory_type:
            entries = [e for e in entries if getattr(e, "memory_type", None) and e.memory_type.value == memory_type]
        return [
            {
                "id": e.id,
                "content": e.content,
                "memory_type": e.memory_type.value if hasattr(e.memory_type, "value") else str(e.memory_type),
                "tags": getattr(e, "tags", []),
                "importance": getattr(e, "importance", None),
                "created_at": getattr(e, "created_at", None),
                "metadata": getattr(e, "metadata", {}),
            }
            for e in entries
        ]

    # ── plans ─────────────────────────────────────────────────────────────

    def list_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        plans = self._plan_store.list_recent(limit=limit)
        return [
            {
                "plan_id": p.plan_id,
                "goal": p.goal,
                "steps": len(p.steps),
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
            }
            for p in plans
        ]

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        plan = self._plan_store.load(plan_id)
        if plan is None:
            return None
        return plan.to_dict() if hasattr(plan, "to_dict") else vars(plan)

    # ── plan runs ─────────────────────────────────────────────────────────

    def list_plan_runs(self, limit: int = 20, status_filter: str | None = None) -> list[dict[str, Any]]:
        plan_runs = self._plan_run_store.list_recent(limit=limit * 3)  # 多拉一些用于过滤
        result = []
        for pr in plan_runs:
            st = pr.status if isinstance(pr.status, str) else str(pr.status)
            if status_filter and st != status_filter:
                continue
            step_runs = getattr(pr, "step_runs", []) or []
            result.append({
                "plan_run_id": pr.plan_run_id,
                "goal": getattr(pr, "goal", ""),
                "status": st,
                "total_steps": len(step_runs),
                "completed_steps": sum(
                    1 for s in step_runs
                    if getattr(s, "status", None) and s.status.value == "completed"
                ),
            })
            if len(result) >= limit:
                break
        return result

    def get_plan_run(self, plan_run_id: str) -> dict[str, Any] | None:
        pr = self._plan_run_store.load(plan_run_id)
        if pr is None:
            return None
        return pr.to_dict() if hasattr(pr, "to_dict") else vars(pr)
