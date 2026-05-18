from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class WorkflowStatus(str, enum.Enum):
    DRAFT = "draft"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowTaskStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass
class Evidence:
    source_type: str = "note"
    summary: str = ""
    content: str | None = None
    uri: str | None = None
    evidence_id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:10]}")
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "summary": self.summary,
            "content": self.content,
            "uri": self.uri,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        return cls(
            evidence_id=str(data.get("evidence_id") or f"ev_{uuid.uuid4().hex[:10]}"),
            source_type=str(data.get("source_type") or data.get("type") or "note"),
            summary=str(data.get("summary") or ""),
            content=data.get("content"),
            uri=data.get("uri"),
            created_at=float(data.get("created_at") or time.time()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Artifact:
    artifact_type: str = "generic"
    summary: str = ""
    path: str | None = None
    uri: str | None = None
    artifact_id: str = field(default_factory=lambda: f"art_{uuid.uuid4().hex[:10]}")
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "summary": self.summary,
            "path": self.path,
            "uri": self.uri,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            artifact_id=str(data.get("artifact_id") or f"art_{uuid.uuid4().hex[:10]}"),
            artifact_type=str(data.get("artifact_type") or data.get("type") or "generic"),
            summary=str(data.get("summary") or ""),
            path=data.get("path"),
            uri=data.get("uri"),
            created_at=float(data.get("created_at") or time.time()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class TaskAttempt:
    task_id: str
    attempt_id: str = field(default_factory=lambda: f"attempt_{uuid.uuid4().hex[:10]}")
    status: WorkflowTaskStatus = WorkflowTaskStatus.RUNNING
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    output: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskAttempt":
        return cls(
            attempt_id=str(data.get("attempt_id") or f"attempt_{uuid.uuid4().hex[:10]}"),
            task_id=str(data["task_id"]),
            status=WorkflowTaskStatus(data.get("status", WorkflowTaskStatus.RUNNING.value)),
            started_at=float(data.get("started_at") or time.time()),
            finished_at=data.get("finished_at"),
            output=data.get("output"),
            error=data.get("error"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class WorkflowTask:
    task_id: str
    title: str
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    suggested_tools: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    status: WorkflowTaskStatus = WorkflowTaskStatus.PENDING
    result_summary: str | None = None
    error: str | None = None
    attempts: list[TaskAttempt] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "step_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "dependencies": self.dependencies,
            "acceptance_criteria": self.acceptance_criteria,
            "suggested_tools": self.suggested_tools,
            "target_files": self.target_files,
            "status": self.status.value,
            "result_summary": self.result_summary,
            "output": self.result_summary,
            "error": self.error,
            "attempts": [item.to_dict() for item in self.attempts],
            "evidence": [item.to_dict() for item in self.evidence],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowTask":
        return cls(
            task_id=str(data.get("task_id") or data.get("step_id")),
            title=str(data.get("title") or data.get("task_id") or data.get("step_id")),
            description=str(data.get("description") or ""),
            dependencies=[str(item) for item in data.get("dependencies", [])],
            acceptance_criteria=[str(item) for item in data.get("acceptance_criteria", [])],
            suggested_tools=[str(item) for item in data.get("suggested_tools", [])],
            target_files=[str(item) for item in data.get("target_files", [])],
            status=WorkflowTaskStatus(data.get("status", WorkflowTaskStatus.PENDING.value)),
            result_summary=data.get("result_summary", data.get("output")),
            error=data.get("error"),
            attempts=[TaskAttempt.from_dict(item) for item in data.get("attempts", []) if isinstance(item, dict)],
            evidence=[Evidence.from_dict(item) for item in data.get("evidence", []) if isinstance(item, dict)],
            artifacts=[Artifact.from_dict(item) for item in data.get("artifacts", []) if isinstance(item, dict)],
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class WorkflowRun:
    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = ""
    context: str = ""
    status: WorkflowStatus = WorkflowStatus.DRAFT
    tasks: list[WorkflowTask] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "context": self.context,
            "status": self.status.value,
            "tasks": [task.to_dict() for task in self.tasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowRun":
        return cls(
            workflow_id=str(data["workflow_id"]),
            goal=str(data.get("goal") or ""),
            context=str(data.get("context") or ""),
            status=WorkflowStatus(data.get("status", WorkflowStatus.DRAFT.value)),
            tasks=[WorkflowTask.from_dict(item) for item in data.get("tasks", []) if isinstance(item, dict)],
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            metadata=dict(data.get("metadata") or {}),
        )
