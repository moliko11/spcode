"""
packages/api/routes/events.py — SSE 事件流端点

GET /api/events/runs/{run_id}                实时订阅 chat run 事件（SSE）
GET /api/events/runs/{run_id}?after_seq=N    断线续传（从 seq=N 之后开始）

GET /api/runs                                列出所有 run（RunManager 中的内存记录）
GET /api/runs/{run_id}                       查询单个 run 状态
DELETE /api/runs/{run_id}                    取消 run
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from packages.api.deps import RunManagerDep
from packages.api.schemas import CancelRunResponse

router = APIRouter(prefix="/api", tags=["events", "runs"])


# ── SSE 事件流 ────────────────────────────────────────────────────────────

@router.get(
    "/events/runs/{run_id}",
    summary="订阅 run 事件流（SSE）",
    response_class=EventSourceResponse,
)
async def stream_run_events(
    run_id: str,
    rm: RunManagerDep,
    after_seq: int = Query(0, ge=0, description="从此 seq 之后开始（断线续传）"),
) -> EventSourceResponse:
    """
    SSE 事件流，每条消息格式：

    ```
    id: <seq>
    event: <event_kind>
    data: {"run_id":"...","seq":N,"kind":"...","step":N,"payload":{...}}
    ```

    客户端断线重连时携带 `Last-Event-ID` 头，服务端从该 seq 之后 replay。
    """
    record = rm.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    async def _generator() -> AsyncIterator[dict[str, Any]]:
        async for event in rm.subscribe(run_id, after_seq=after_seq):
            kind = event.get("event_kind") or event.get("event_type", "unknown")
            seq = event.get("seq", 0)
            yield {
                "id": str(seq),
                "event": kind,
                "data": json.dumps(event, ensure_ascii=False),
            }

    return EventSourceResponse(_generator(), ping=15)


# ── Run 管理端点（RunManager 内存记录）────────────────────────────────────

@router.get("/runs", summary="列出 RunManager 中的 run 记录")
async def list_runs(
    rm: RunManagerDep,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    return rm.list_runs(limit=limit, status_filter=status_filter)


@router.get("/runs/{run_id}", summary="查询 run 状态")
async def get_run(run_id: str, rm: RunManagerDep) -> dict[str, Any]:
    record = rm.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return record


@router.delete(
    "/runs/{run_id}",
    response_model=CancelRunResponse,
    summary="取消 run",
)
async def cancel_run(run_id: str, rm: RunManagerDep) -> CancelRunResponse:
    cancelled = rm.cancel(run_id)
    return CancelRunResponse(run_id=run_id, cancelled=cancelled)
