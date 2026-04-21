from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class StepStatus(str, enum.Enum):
    """单个步骤的执行状态"""
    PENDING = "pending"       # 尚未开始
    READY = "ready"           # 依赖已满足，可执行
    RUNNING = "running"       # 执行中
    COMPLETED = "completed"   # 成功完成
    FAILED = "failed"         # 执行失败
    SKIPPED = "skipped"       # 被跳过（前置步骤失败）


class PlanStatus(str, enum.Enum):
    """整体计划的状态"""
    DRAFT = "draft"           # 刚生成，待审阅
    APPROVED = "approved"     # 已批准，可执行
    RUNNING = "running"       # 执行中
    COMPLETED = "completed"   # 全部步骤完成
    FAILED = "failed"         # 执行失败或被中止
    REPLANNING = "replanning" # 重规划中


@dataclass
class TaskStep:
    """计划中的一个执行步骤"""
    step_id: str
    title: str
    description: str
    # 依赖的 step_id 列表：这些步骤完成后本步骤才能执行
    dependencies: list[str] = field(default_factory=list)
    # 完成质量验收标准（纯文本，由 Verifier 对照）
    acceptance_criteria: list[str] = field(default_factory=list)
    # 建议使用的工具名称（可选）
    suggested_tools: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    output: str | None = None
    error: str | None = None
    # 元数据（预留扩展）
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "description": self.description,
            "dependencies": self.dependencies,
            "acceptance_criteria": self.acceptance_criteria,
            "suggested_tools": self.suggested_tools,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskStep":
        return cls(
            step_id=data["step_id"],
            title=data["title"],
            description=data["description"],
            dependencies=data.get("dependencies", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            suggested_tools=data.get("suggested_tools", []),
            status=StepStatus(data.get("status", StepStatus.PENDING.value)),
            output=data.get("output"),
            error=data.get("error"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class TaskPlan:
    """由 Planner 生成的完整任务计划"""
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = ""
    context: str = ""  # 规划时注入的背景信息（记忆摘要等）
    steps: list[TaskStep] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "context": self.context,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskPlan":
        plan = cls(
            plan_id=data["plan_id"],
            goal=data["goal"],
            context=data.get("context", ""),
            steps=[TaskStep.from_dict(s) for s in data.get("steps", [])],
            status=PlanStatus(data.get("status", PlanStatus.DRAFT.value)),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            metadata=data.get("metadata", {}),
        )
        return plan
