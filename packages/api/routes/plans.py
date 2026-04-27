"""
packages/api/routes/plans.py — Plan 与 Plan Run 端点

POST /api/plans                           创建计划
GET  /api/plans                           列表
GET  /api/plans/{plan_id}                 详情

POST /api/plan-runs                       启动计划执行
GET  /api/plan-runs                       列表（支持 status 过滤）
GET  /api/plan-runs/{plan_run_id}         详情
POST /api/plan-runs/{plan_run_id}/approve 审批
POST /api/plan-runs/{plan_run_id}/reject  拒绝
POST /api/plan-runs/{plan_run_id}/recover 从失败恢复
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from packages.api.deps import OrchestrateDep, PlanServiceDep, QueryServiceDep
from packages.api.schemas import (
    CreatePlanRequest,
    PlanResponse,
    PlanRunApproveRequest,
    PlanRunResponse,
    StartPlanRunRequest,
)

router = APIRouter(tags=["plans"])


# ── helper ────────────────────────────────────────────────────────────────

def _plan_response(plan: object) -> PlanResponse:
    d = plan.to_dict() if hasattr(plan, "to_dict") else {}
    steps = getattr(plan, "steps", []) or []
    return PlanResponse(
        plan_id=getattr(plan, "plan_id", ""),
        goal=getattr(plan, "goal", ""),
        status=str(getattr(plan, "status", "created")),
        step_count=len(steps),
        raw=d,
    )


def _pr_response(summary: object) -> PlanRunResponse:
    from packages.app_service.orchestrate_service import PlanRunSummary
    if isinstance(summary, PlanRunSummary):
        return PlanRunResponse(
            plan_run_id=summary.plan_run_id,
            status=summary.status,
            goal=summary.goal,
            total_steps=summary.total_steps,
            completed_steps=summary.completed_steps,
            failed_steps=summary.failed_steps,
            waiting_human=summary.waiting_human,
            waiting_step=summary.waiting_step,
            cost_summary=summary.cost_summary,
            raw=summary.raw,
        )
    # 兼容 dict（QueryService 返回的历史记录）
    d = summary if isinstance(summary, dict) else {}
    return PlanRunResponse(
        plan_run_id=d.get("plan_run_id", ""),
        status=d.get("status", ""),
        goal=d.get("goal", ""),
        total_steps=d.get("total_steps", 0),
        completed_steps=d.get("completed_steps", 0),
        failed_steps=d.get("failed_steps", 0),
        waiting_human=d.get("status") == "waiting_human",
        raw=d,
    )


# ── Plans ─────────────────────────────────────────────────────────────────

plan_router = APIRouter(prefix="/api/plans")


@plan_router.post(
    "",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="生成计划",
)
async def create_plan(body: CreatePlanRequest, svc: PlanServiceDep) -> PlanResponse:
    plan = await svc.create_plan(goal=body.goal, user_id=body.user_id, context=body.context)
    return _plan_response(plan)


@plan_router.get("", response_model=list[PlanResponse], summary="列出计划")
async def list_plans(qs: QueryServiceDep, limit: int = Query(20, ge=1, le=200)) -> list[PlanResponse]:
    plans = qs.list_plans(limit=limit)
    return [PlanResponse(**p) if isinstance(p, dict) else _plan_response(p) for p in plans]


@plan_router.get("/{plan_id}", response_model=PlanResponse, summary="计划详情")
async def get_plan(plan_id: str, qs: QueryServiceDep) -> PlanResponse:
    plan = qs.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"plan not found: {plan_id}")
    return PlanResponse(**plan) if isinstance(plan, dict) else _plan_response(plan)


# ── Plan Runs ─────────────────────────────────────────────────────────────

run_router = APIRouter(prefix="/api/plan-runs")


@run_router.post(
    "",
    response_model=PlanRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="执行计划",
)
async def start_plan_run(body: StartPlanRunRequest, svc: OrchestrateDep) -> PlanRunResponse:
    summary = await svc.run(
        plan_id=body.plan_id,
        user_id=body.user_id,
        context=body.context,
    )
    return _pr_response(summary)


@run_router.get("", response_model=list[PlanRunResponse], summary="列出 plan run")
async def list_plan_runs(
    qs: QueryServiceDep,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=200),
) -> list[PlanRunResponse]:
    runs = qs.list_plan_runs(status_filter=status_filter, limit=limit)
    return [_pr_response(r) for r in runs]


@run_router.get("/{plan_run_id}", response_model=PlanRunResponse, summary="plan run 详情")
async def get_plan_run(plan_run_id: str, qs: QueryServiceDep) -> PlanRunResponse:
    pr = qs.get_plan_run(plan_run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail=f"plan_run not found: {plan_run_id}")
    return _pr_response(pr)


@run_router.post("/{plan_run_id}/approve", response_model=PlanRunResponse, summary="审批")
async def approve_plan_run(
    plan_run_id: str,
    body: PlanRunApproveRequest,
    svc: OrchestrateDep,
) -> PlanRunResponse:
    summary = await svc.approve(
        plan_run_id=plan_run_id,
        approved=body.approved,
        edited_arguments=body.edited_arguments,
    )
    return _pr_response(summary)


@run_router.post("/{plan_run_id}/reject", response_model=PlanRunResponse, summary="拒绝")
async def reject_plan_run(plan_run_id: str, svc: OrchestrateDep) -> PlanRunResponse:
    summary = await svc.approve(plan_run_id=plan_run_id, approved=False)
    return _pr_response(summary)


@run_router.post("/{plan_run_id}/recover", response_model=PlanRunResponse, summary="从失败恢复")
async def recover_plan_run(plan_run_id: str, svc: OrchestrateDep) -> PlanRunResponse:
    summary = await svc.recover(plan_run_id=plan_run_id)
    return _pr_response(summary)


# 合并两个子路由，在 fastapi_app.py 里一次 include
router = APIRouter()
router.include_router(plan_router)
router.include_router(run_router)
