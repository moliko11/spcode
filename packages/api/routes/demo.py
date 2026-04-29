"""
packages/api/routes/demo.py — 最小 Web demo 页面

提供一个可直接访问的页面，用于验证 chat SSE/streaming 的最小消费链路。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter(tags=["demo"])

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_CHAT_DEMO_HTML = _STATIC_DIR / "chat_demo.html"


@router.get("/demo/chat", summary="最小 Chat Web Demo")
async def chat_demo_page() -> FileResponse:
    return FileResponse(_CHAT_DEMO_HTML)