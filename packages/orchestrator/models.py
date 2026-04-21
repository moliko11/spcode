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
    output: str | None = None
    error: str | None = None
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
            "output": self.output,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
        }


@dataclass
class PlanRun:
    """整个计划的一次执行记录"""
    plan_run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str = ""
    goal: str = ""
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
            "step_runs": [s.to_dict() for s in self.step_runs],
            "completed": self.completed,
            "final_output": self.final_output,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
