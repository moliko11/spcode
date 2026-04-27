"""
packages/api/routes/query.py — 历史查询端点

GET /api/sessions/{session_id}/messages    会话消息
GET /api/sessions                          会话列表
GET /api/users/{user_id}/memories          用户记忆
GET /api/audit/events                      审计日志（文件流）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from packages.api.deps import QueryServiceDep
from packages.runtime.config import AUDIT_LOG_PATH

router = APIRouter(prefix="/api", tags=["query"])


@router.get("/sessions", summary="列出会话")
async def list_sessions(
    qs: QueryServiceDep,
    limit: int = Query(20, ge=1, le=200),
) -> list[dict[str, Any]]:
    return qs.list_sessions(limit=limit)


@router.get("/sessions/{session_id}/messages", summary="会话消息")
async def get_session_messages(
    session_id: str,
    qs: QueryServiceDep,
) -> list[dict[str, Any]]:
    messages = await qs.get_session_messages(session_id)
    if messages is None:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    return messages


@router.get("/users/{user_id}/memories", summary="用户记忆列表")
async def list_memories(
    user_id: str,
    qs: QueryServiceDep,
    memory_type: Optional[str] = Query(None, description="episode|semantic|procedural"),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    memories = await qs.list_memories(user_id=user_id, limit=limit, memory_type=memory_type)
    return memories


@router.get("/audit/events", summary="流式读取审计日志")
async def stream_audit_events(
    limit: int = Query(200, ge=1, le=5000),
) -> StreamingResponse:
    """
    以 NDJSON 格式返回最新 N 条审计记录（从文件尾部读取）。
    """
    path: Path = AUDIT_LOG_PATH

    def _iter():
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                yield line + "\n"

    return StreamingResponse(
        _iter(),
        media_type="application/x-ndjson",
    )
