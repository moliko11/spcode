from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class StepRunStatus(str, enum.Enum):
    """单个步骤的执行运行状态"""
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StepRun:
    """一个 TaskStep 的实际执行记录"""
    step_id: str
    title: str
    status: StepRunStatus = StepRunStatus.PENDING
    run_id: str | None = None          # 对应的 AgentRuntime.chat() run_id
    step_session_id: str | None = None # 本步骤使用的 session_id（并行时各步独立）
    output: str | None = None
    error: str | None = None
    pending_human_request: dict[str, Any] | None = None
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def duration_s(self) -> float | None:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 3)
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "status": self.status.value,
            "run_id": self.run_id,
            "step_session_id": self.step_session_id,
            "output": self.output,
            "error": self.error,
            "pending_human_request": self.pending_human_request,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StepRun":
        return cls(
            step_id=data["step_id"],
            title=data["title"],
            status=StepRunStatus(data.get("status", StepRunStatus.PENDING.value)),
            run_id=data.get("run_id"),
            step_session_id=data.get("step_session_id"),
            output=data.get("output"),
            error=data.get("error"),
            pending_human_request=data.get("pending_human_request"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )


@dataclass
class PlanRun:
    """整个计划的一次执行记录"""
    plan_run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str = ""
    goal: str = ""
    status: str = "running"
    session_id: str = ""
    current_step_index: int = 0
    pending_step_id: str | None = None
    step_runs: list[StepRun] = field(default_factory=list)
    completed: bool = False
    final_output: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_run_id": self.plan_run_id,
            "plan_id": self.plan_id,
            "goal": self.goal,
            "status": self.status,
            "session_id": self.session_id,
            "current_step_index": self.current_step_index,
            "pending_step_id": self.pending_step_id,
            "step_runs": [s.to_dict() for s in self.step_runs],
            "completed": self.completed,
            "final_output": self.final_output,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanRun":
        return cls(
            plan_run_id=data["plan_run_id"],
            plan_id=data["plan_id"],
            goal=data.get("goal", ""),
            status=data.get("status", "running"),
            session_id=data.get("session_id", ""),
            current_step_index=data.get("current_step_index", 0),
            pending_step_id=data.get("pending_step_id"),
            step_runs=[StepRun.from_dict(item) for item in data.get("step_runs", [])],
            completed=data.get("completed", False),
            final_output=data.get("final_output", ""),
            started_at=data.get("started_at", time.time()),
            finished_at=data.get("finished_at"),
        )
