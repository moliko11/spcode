"""
packages/api/fastapi_app.py — FastAPI 应用主入口

启动方式（开发）：
    uv run agent serve api --reload

直接 uvicorn（生产）：
    uv run uvicorn packages.api.fastapi_app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from packages.app_service.run_service import RunManager
from packages.api.routes.chat import router as chat_router
from packages.api.routes.events import router as events_router
from packages.api.routes.plans import router as plans_router
from packages.api.routes.query import router as query_router


# ── lifespan：注册单例 RunManager ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.run_manager = RunManager()
    yield
    # shutdown：可在此做清理（目前 RunManager 无需显式释放）


# ── 应用实例 ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Agent Leaning API",
    version="0.1.0",
    description=(
        "Personal Code Agent REST + SSE API。\n\n"
        "- **Chat**：同步/异步 chat run\n"
        "- **Plans**：生成、查询、执行、审批 plan\n"
        "- **Events**：SSE 实时订阅 run 事件流\n"
        "- **Query**：历史会话、记忆、审计日志\n"
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS（仅允许本地开发前端，生产可锁定 origins）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ── 全局异常处理 ──────────────────────────────────────────────────────────

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc), "error_type": "ValueError"},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_type": type(exc).__name__},
    )


# ── 注册路由 ──────────────────────────────────────────────────────────────

app.include_router(chat_router)
app.include_router(events_router)
app.include_router(plans_router)
app.include_router(query_router)


# ── 健康检查 ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
async def health() -> dict[str, Any]:
    return {"status": "ok", "version": app.version}


@app.get("/api/tools", tags=["meta"], summary="列出已注册工具的元数据")
async def list_tools() -> dict[str, Any]:
    """
    返回 ToolRegistry 中所有工具的 ToolSpec 摘要。
    前端用于确认 render_kind 默认值和 ui_category。
    """
    from packages.runtime.bootstrap import build_runtime
    from packages.runtime.models import to_jsonable
    try:
        runtime = build_runtime()
        specs = {
            name: to_jsonable(spec)
            for name, spec in runtime.registry._specs.items()
        }
        return {"tools": specs, "count": len(specs)}
    except Exception as exc:
        return {"tools": {}, "count": 0, "error": str(exc)}
