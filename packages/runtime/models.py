from __future__ import annotations

import dataclasses
import enum
import json
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


class GuardrailViolation(Exception):
    pass


class PermissionDenied(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class HumanInterventionRequired(Exception):
    def __init__(self, request: "HumanInterventionRequest") -> None:
        super().__init__(request.reason)
        self.request = request


class RunStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    TOOL_RUNNING = "tool_running"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Phase(str, enum.Enum):
    DECIDING = "deciding"
    TOOL_PENDING = "tool_pending"
    TOOL_EXECUTED = "tool_executed"
    WAITING_HUMAN = "waiting_human"
    FINALIZING = "finalizing"
    COMPLETED = "completed"


class EventType(str, enum.Enum):
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    STEP_STARTED = "step_started"
    MODEL_OUTPUT = "model_output"
    TOOL_SELECTED = "tool_selected"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    TOOL_FAILED = "tool_failed"
    HUMAN_REQUIRED = "human_required"
    HUMAN_APPROVED = "human_approved"
    HUMAN_REJECTED = "human_rejected"
    STEP_FINISHED = "step_finished"
    CHECKPOINT_SAVED = "checkpoint_saved"
    RUN_COMPLETED = "run_completed"
    RUN_DEGRADED = "run_degraded"
    RUN_FAILED = "run_failed"
    CONTEXT_SNIPPED = "context_snipped"
    MICROCOMPACT_APPLIED = "microcompact_applied"


@dataclass(slots=True)
class SessionMessage:
    role: str
    content: str
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class ToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    idempotency_key: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    call_id: str
    tool_name: str
    ok: bool
    output: str | None = None
    error: str | None = None
    latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    retry_count: int = 0
    from_cache: bool = False
    approved_by: str | None = None
    sandbox_mode: str | None = None


@dataclass(slots=True)
class AgentEvent:
    run_id: str
    event_type: EventType
    ts: float
    step: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HumanInterventionRequest:
    reason: str
    context: dict[str, Any]
    suggested_actions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StepRecord:
    step: int
    phase: str
    raw_content: str
    raw_tool_calls: list[dict[str, Any]]
    model_name: str


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    allowed_roles: list[str] = field(default_factory=list)
    require_approval: bool = False
    timeout_s: float = 10.0
    readonly: bool = True
    risk_level: str = "low"
    side_effect: str = "none"
    sandbox_required: bool = False
    network_required: bool = False
    writes_workspace: bool = False
    cache_policy: str = "success_only"
    max_retries: int | None = None
    approval_policy: str = "never"

    def __post_init__(self) -> None:
        if self.require_approval and self.approval_policy == "never":
            self.approval_policy = "always"


@dataclass(slots=True)
class ShellToolSpec(ToolSpec):
    allowed_commands: list[str] = field(default_factory=list)
    blocked_patterns: list[str] = field(
        default_factory=lambda: [
            r"\brm\b",
            r"\bdel\b",
            r"\bRemove-Item\b",
            r"\bformat\b",
            r"git\s+reset\s+--hard",
            r"git\s+checkout\s+--",
        ]
    )
    working_dir_mode: str = "workspace_only"
    env_policy: str = "safe_default"
    shell_mode: str = "powershell"
    capture_output_limit: int = 20_000


@dataclass(slots=True)
class AgentState:
    run_id: str
    user_id: str
    task: str
    session_id: str
    status: RunStatus = RunStatus.IDLE
    phase: Phase = Phase.DECIDING
    step: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history: list[StepRecord] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    final_output: str | None = None
    failure_reason: str | None = None
    conversation: list[SessionMessage] = field(default_factory=list)
    runtime_messages: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_call: dict[str, Any] | None = None
    pending_tool_result: dict[str, Any] | None = None
    pending_human_request: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrchestrationTurn:
    turn_index: int
    output: str
    run_id: str
    status: str


@dataclass(slots=True)
class OrchestrationResult:
    turns: list[OrchestrationTurn]
    completed: bool
    final_output: str


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, enum.Enum):
        return obj.value
    if dataclasses.is_dataclass(obj):
        return {k: to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj


def safe_json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def serialize_message(msg: Any) -> dict[str, Any]:
    if isinstance(msg, SystemMessage):
        return {"type": "system", "content": msg.content}
    if isinstance(msg, HumanMessage):
        return {"type": "human", "content": msg.content}
    if isinstance(msg, ToolMessage):
        return {"type": "tool", "content": msg.content, "tool_call_id": msg.tool_call_id}
    if isinstance(msg, AIMessage):
        return {
            "type": "ai",
            "content": msg.content,
            "tool_calls": getattr(msg, "tool_calls", []) or [],
            "additional_kwargs": getattr(msg, "additional_kwargs", {}) or {},
        }
    raise TypeError(f"unsupported message type: {type(msg).__name__}")


def deserialize_message(data: dict[str, Any]) -> Any:
    msg_type = data["type"]
    if msg_type == "system":
        return SystemMessage(content=data["content"])
    if msg_type == "human":
        return HumanMessage(content=data["content"])
    if msg_type == "tool":
        return ToolMessage(content=data["content"], tool_call_id=data["tool_call_id"])
    if msg_type == "ai":
        return AIMessage(
            content=data.get("content", ""),
            tool_calls=data.get("tool_calls", []) or [],
            additional_kwargs=data.get("additional_kwargs", {}) or {},
        )
    raise ValueError(f"unknown message type: {msg_type}")


def normalize_tool_message(result: ToolResult) -> str:
    if result.ok:
        return result.output or result.stdout or ""
    return result.error or result.stderr or "tool execution failed"
