"""
ChatService — 管理单轮/多轮 chat run 的整个生命周期。

职责：
- 调用 AgentRuntime.chat()
- 处理 waiting_human 审批循环
- 把 AgentState 整形为 ChatRunResult（给 CLI/Web 用，不暴露内部数据类）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from packages.runtime.agent_loop import AgentRuntime
from packages.runtime.bootstrap import build_runtime, build_llm
from packages.runtime.models import AgentState, RunStatus


@dataclass
class HumanDecision:
    """人工审批决策"""
    approved: bool
    approved_by: str = "human"
    edited_arguments: dict[str, Any] | None = None


@dataclass
class ChatRunResult:
    """chat() 调用结果，对 CLI/Web 友好的数据结构"""
    run_id: str
    status: str
    final_output: str | None
    failure_reason: str | None
    cost_summary: dict[str, Any]
    pending_human_request: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("completed", "degraded")

    @property
    def waiting_human(self) -> bool:
        return self.status == "waiting_human"


def _state_to_result(state: AgentState) -> ChatRunResult:
    metadata = getattr(state, "metadata", {}) or {}
    cost_summary = metadata.get("cost_summary", {}) or {}
    return ChatRunResult(
        run_id=state.run_id,
        status=state.status.value,
        final_output=state.final_output,
        failure_reason=state.failure_reason,
        cost_summary=cost_summary,
        pending_human_request=state.pending_human_request,
    )


class ChatService:
    """
    Chat 业务服务。

    usage::

        svc = ChatService.from_env()
        result = await svc.chat(user_id="demo", session_id="s1", message="hello")
        if result.waiting_human:
            result = await svc.approve(result.run_id, HumanDecision(approved=True))
    """

    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime

    # ── factory ──────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, provider: str | None = None, max_tool_calls: int | None = None) -> "ChatService":
        """从环境变量构建（与 main.py 的 configure_provider 对齐）。"""
        import os
        if provider:
            os.environ["MOLIKO_LLM_PROVIDER"] = provider
        runtime = build_runtime(max_tool_calls=max_tool_calls)
        return cls(runtime=runtime)

    # ── core API ─────────────────────────────────────────────────────────

    async def chat(
        self,
        user_id: str,
        session_id: str,
        message: str,
    ) -> ChatRunResult:
        """执行一轮 chat，返回结果（含 waiting_human 状态）。"""
        state = await self._runtime.chat(
            user_id=user_id,
            session_id=session_id,
            message=message,
        )
        return _state_to_result(state)

    async def approve(
        self,
        run_id: str,
        decision: HumanDecision,
    ) -> ChatRunResult:
        """对处于 waiting_human 的 run 做审批并继续执行。"""
        state = await self._runtime.resume(
            run_id=run_id,
            human_decision={
                "approved": decision.approved,
                "approved_by": decision.approved_by,
                "edited_arguments": decision.edited_arguments,
            },
        )
        return _state_to_result(state)

    async def chat_with_approvals(
        self,
        user_id: str,
        session_id: str,
        message: str,
        on_approval_needed: "ApprovalCallback | None" = None,
    ) -> ChatRunResult:
        """
        chat + 自动审批循环。

        on_approval_needed: 异步回调，接收 pending_request: dict，
        返回 HumanDecision。若为 None，默认自动批准低风险工具。
        """
        result = await self.chat(user_id=user_id, session_id=session_id, message=message)
        while result.waiting_human and result.pending_human_request is not None:
            if on_approval_needed is not None:
                decision = await on_approval_needed(result.pending_human_request)
            else:
                decision = HumanDecision(approved=True)
            result = await self.approve(result.run_id, decision)
        return result

    # ── session helpers ───────────────────────────────────────────────────

    async def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        """返回会话历史消息列表（供 show-session / API 用）。"""
        if self._runtime.session_store is None:
            return []
        messages = await self._runtime.session_store.load_messages(session_id)
        return [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in messages]


# 类型别名
from typing import Callable, Awaitable
ApprovalCallback = Callable[[dict[str, Any]], Awaitable[HumanDecision]]
