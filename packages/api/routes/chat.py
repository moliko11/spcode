"""
packages/api/routes/chat.py — Chat 相关 REST 端点

POST /api/chat/runs        同步 chat（等待完成）
GET  /api/chat/runs/{id}   查询 run 状态
POST /api/chat/runs/{id}/approve  审批/恢复
DELETE /api/chat/runs/{id}        取消
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, status
from sse_starlette.sse import EventSourceResponse

from packages.app_service.chat_service import ChatService, HumanDecision
from packages.api.deps import ChatServiceDep, RunManagerDep
from packages.api.schemas import (
    ApproveRequest,
    CancelRunResponse,
    ChatRequest,
    ChatRunResponse,
    StartChatRunRequest,
    StartChatRunResponse,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _to_response(result: "ChatRunResult") -> ChatRunResponse:  # type: ignore[name-defined]
    return ChatRunResponse(
        run_id=result.run_id,
        status=result.status,
        final_output=result.final_output,
        failure_reason=result.failure_reason,
        cost_summary=result.cost_summary,
        pending_human_request=result.pending_human_request,
        waiting_human=result.waiting_human,
    )


# ── 同步 chat（直到完成或 waiting_human）─────────────────────────────────

@router.post(
    "/runs",
    response_model=ChatRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="发起一次同步 chat run",
)
async def start_chat_run(
    body: ChatRequest,
    svc: ChatServiceDep,
) -> ChatRunResponse:
    """
    同步执行 chat，阻塞直到 completed / waiting_human / failed。
    适合轻量交互；大模型响应较慢时建议用 POST /api/runs/chat（异步）。
    """
    result = await svc.chat(
        user_id=body.user_id,
        session_id=body.session_id,
        message=body.message,
    )
    return _to_response(result)


@router.post(
    "/stream",
    summary="发起一次流式 chat run（SSE）",
    response_class=EventSourceResponse,
)
async def stream_chat_run(
    body: ChatRequest,
    rm: RunManagerDep,
    svc: ChatServiceDep,
    after_seq: int = Query(0, ge=0, description="从此 seq 之后开始（断线续传）"),
) -> EventSourceResponse:
    run_id = await rm.start_chat(
        chat_service=svc,
        user_id=body.user_id,
        session_id=body.session_id,
        message=body.message,
    )

    async def _generator() -> AsyncIterator[dict[str, Any]]:
        async for event in rm.subscribe(run_id, after_seq=after_seq):
            kind = event.get("event_kind") or event.get("kind", "unknown")
            seq = event.get("seq", 0)
            payload = {"run_id": run_id, **event}
            yield {
                "id": str(seq),
                "event": kind,
                "data": json.dumps(payload, ensure_ascii=False),
            }

    return EventSourceResponse(_generator(), ping=15)


@router.get(
    "/runs/{run_id}",
    response_model=ChatRunResponse,
    summary="查询 chat run 状态",
)
async def get_chat_run(
    run_id: str,
    svc: ChatServiceDep,
) -> ChatRunResponse:
    """从 checkpoint store 恢复 run 快照（只读）。"""
    state = svc._runtime.checkpoint_store.load(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    from packages.app_service.chat_service import _state_to_result
    return _to_response(_state_to_result(state))


@router.post(
    "/runs/{run_id}/approve",
    response_model=ChatRunResponse,
    summary="审批并恢复 waiting_human run",
)
async def approve_chat_run(
    run_id: str,
    body: ApproveRequest,
    svc: ChatServiceDep,
) -> ChatRunResponse:
    decision = HumanDecision(
        approved=body.approved,
        approved_by=body.approved_by,
        edited_arguments=body.edited_arguments,
    )
    result = await svc.approve(run_id, decision)
    if result is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return _to_response(result)


@router.delete(
    "/runs/{run_id}",
    response_model=CancelRunResponse,
    summary="取消正在运行的 chat run",
)
async def cancel_chat_run(
    run_id: str,
    rm: RunManagerDep,
) -> CancelRunResponse:
    cancelled = rm.cancel(run_id)
    return CancelRunResponse(run_id=run_id, cancelled=cancelled)


# ── 异步 chat（立即返回 run_id，后续通过 SSE 订阅）──────────────────────

@router.post(
    "/async-runs",
    response_model=StartChatRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="发起异步 chat run（立即返回 run_id）",
)
async def start_async_chat_run(
    body: StartChatRunRequest,
    rm: RunManagerDep,
    svc: ChatServiceDep,
) -> StartChatRunResponse:
    run_id = await rm.start_chat(
        chat_service=svc,
        user_id=body.user_id,
        session_id=body.session_id,
        message=body.message,
    )
    return StartChatRunResponse(run_id=run_id)
