"""
packages/api/schemas.py — 请求/响应 Pydantic 模型

原则：
- 所有 API 边界使用 Pydantic BaseModel，不暴露内部 dataclass
- 字段命名统一 snake_case
- 带有 Optional 的字段提供默认值，减少客户端必填项
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── Chat ──────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=32_000)
    user_id: str = Field("demo-user", min_length=1, max_length=128)
    session_id: str = Field("demo-session", min_length=1, max_length=128)


class ChatRunResponse(BaseModel):
    run_id: str
    status: str
    final_output: Optional[str] = None
    failure_reason: Optional[str] = None
    cost_summary: dict[str, Any] = Field(default_factory=dict)
    pending_human_request: Optional[dict[str, Any]] = None
    waiting_human: bool = False


class ApproveRequest(BaseModel):
    approved: bool
    approved_by: str = "api-user"
    edited_arguments: Optional[dict[str, Any]] = None


# ── Plan ─────────────────────────────────────────────────────────────────

class CreatePlanRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=8_000)
    user_id: str = "demo-user"
    context: str = ""


class PlanResponse(BaseModel):
    plan_id: str
    goal: str
    status: str
    step_count: int
    raw: dict[str, Any] = Field(default_factory=dict)


# ── Plan Run ─────────────────────────────────────────────────────────────

class StartPlanRunRequest(BaseModel):
    plan_id: str
    user_id: str = "demo-user"
    context: str = ""


class PlanRunResponse(BaseModel):
    plan_run_id: str
    status: str
    goal: str
    total_steps: int
    completed_steps: int
    failed_steps: int
    waiting_human: bool = False
    waiting_step: Optional[dict[str, Any]] = None
    cost_summary: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class PlanRunApproveRequest(BaseModel):
    approved: bool
    approved_by: str = "api-user"
    edited_arguments: Optional[dict[str, Any]] = None


# ── Async Run (RunManager) ────────────────────────────────────────────────

class StartChatRunRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=32_000)
    user_id: str = "demo-user"
    session_id: str = "demo-session"


class StartChatRunResponse(BaseModel):
    run_id: str
    status: Literal["queued"] = "queued"


class CancelRunResponse(BaseModel):
    run_id: str
    cancelled: bool


# ── Shared ────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    detail: str
    error_type: Optional[str] = None
