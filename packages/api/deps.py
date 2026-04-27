"""
packages/api/deps.py — FastAPI 依赖注入（单例服务、请求级依赖）

- 服务单例用 @lru_cache 懒初始化，保证进程内只建一个 ChatService / OrchestrateService 等
- RunManager 在 lifespan 里注册，供路由通过 Annotated[RunManager, Depends(get_run_manager)] 使用
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from packages.app_service.chat_service import ChatService
from packages.app_service.orchestrate_service import OrchestrateService
from packages.app_service.plan_service import PlanService
from packages.app_service.query_service import QueryService
from packages.app_service.run_service import RunManager


# ── 单例工厂 ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _chat_service() -> ChatService:
    return ChatService.from_env()


@lru_cache(maxsize=1)
def _orchestrate_service() -> OrchestrateService:
    return OrchestrateService.from_env()


@lru_cache(maxsize=1)
def _plan_service() -> PlanService:
    return PlanService.from_env()


@lru_cache(maxsize=1)
def _query_service() -> QueryService:
    return QueryService.from_env()


# ── Depends 函数 ──────────────────────────────────────────────────────────

def get_chat_service() -> ChatService:
    return _chat_service()


def get_orchestrate_service() -> OrchestrateService:
    return _orchestrate_service()


def get_plan_service() -> PlanService:
    return _plan_service()


def get_query_service() -> QueryService:
    return _query_service()


def get_run_manager(request: Request) -> RunManager:
    """从 app.state 取已注册的 RunManager 单例。"""
    rm: RunManager | None = getattr(request.app.state, "run_manager", None)
    if rm is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RunManager not initialized",
        )
    return rm


# ── 类型别名 ──────────────────────────────────────────────────────────────

ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
OrchestrateDep = Annotated[OrchestrateService, Depends(get_orchestrate_service)]
PlanServiceDep = Annotated[PlanService, Depends(get_plan_service)]
QueryServiceDep = Annotated[QueryService, Depends(get_query_service)]
RunManagerDep = Annotated[RunManager, Depends(get_run_manager)]
