from __future__ import annotations

import dataclasses
import enum
import json
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


class GuardrailViolation(Exception):
    """
    障碍物异常
    """
    pass


class PermissionDenied(Exception):
    """
    权限拒绝异常
    """
    pass


class BudgetExceeded(Exception):
    """
    预算超出异常
    """
    pass


class HumanInterventionRequired(Exception):
    """
    人工干预需要异常
    """
    def __init__(self, request: "HumanInterventionRequest") -> None:
        super().__init__(request.reason)
        self.request = request


class RunStatus(str, enum.Enum):
    """
    运行状态
    """
    IDLE = "idle" # 空闲
    RUNNING = "running" # 运行中
    TOOL_RUNNING = "tool_running" # 工具运行中
    WAITING_HUMAN = "waiting_human" # 等待人工干预
    COMPLETED = "completed" # 完成
    DEGRADED = "degraded" # 降级
    FAILED = "failed" # 失败
    CANCELLED = "cancelled" # 巖消


class Phase(str, enum.Enum):
    """
    步骤阶段
    """
    DECIDING = "deciding" # 决策中
    TOOL_PENDING = "tool_pending" # 工具待调用
    TOOL_EXECUTED = "tool_executed" # 工具已调用
    WAITING_HUMAN = "waiting_human" # 等待人工干预
    FINALIZING = "finalizing" # 最终化中
    COMPLETED = "completed" # 完成


class EventType(str, enum.Enum):
    """
    事件类型
    """
    RUN_STARTED = "run_started" # 运行开始
    RUN_RESUMED = "run_resumed" # 运行恢复
    STEP_STARTED = "step_started" # 步骤开始
    MODEL_OUTPUT = "model_output" # 模型输出
    TOOL_SELECTED = "tool_selected" # 工具选择
    TOOL_STARTED = "tool_started" # 工具开始
    TOOL_FINISHED = "tool_finished" # 工具完成
    TOOL_FAILED = "tool_failed" # 工具失败
    HUMAN_REQUIRED = "human_required" # 人工干预需要
    HUMAN_APPROVED = "human_approved" # 人工干预审批
    HUMAN_REJECTED = "human_rejected" # 人工干预拒绝
    STEP_FINISHED = "step_finished" # 步骤完成
    CHECKPOINT_SAVED = "checkpoint_saved" # 检查点保存
    RUN_COMPLETED = "run_completed" # 运行完成
    RUN_DEGRADED = "run_degraded" # 运行降级
    RUN_FAILED = "run_failed" # 运行失败
    CONTEXT_SNIPPED = "context_snipped" # 上下文截取
    MICROCOMPACT_APPLIED = "microcompact_applied" # 微压缩应用
    MEMORY_RECALLED = "memory_recalled" # 内存召回
    MEMORY_STORED = "memory_stored" # 内存存储
    AUTOCOMPACT_APPLIED = "autocompact_applied" # 自动压缩应用


@dataclass(slots=True)
class SessionMessage:
    """
    会话消息
    """
    role: str # 角色
    content: str # 内容
    created_at: float = field(default_factory=time.time) # 创建时间


@dataclass(slots=True)
class ToolCall:
    """
    工具调用
    """
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    idempotency_key: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    """
    工具结果
    """
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
    """
    agent事件
    """
    run_id: str
    event_type: EventType
    ts: float
    step: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HumanInterventionRequest:
    """
    人工干预请求
    """
    reason: str
    context: dict[str, Any]
    suggested_actions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StepRecord:
    """
    步骤记录
    """
    step: int
    phase: str
    raw_content: str
    raw_tool_calls: list[dict[str, Any]]
    model_name: str


@dataclass(slots=True)
class ToolSpec:
    """
    工具规格
    """
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
    """
    shell工具规格
    """
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
    """
    agent状态
    """
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
    """
    chestration轮
    """
    turn_index: int
    output: str
    run_id: str
    status: str


@dataclass(slots=True)
class OrchestrationResult:
    """
    chestration结果
    """
    turns: list[OrchestrationTurn]
    completed: bool
    final_output: str


def to_jsonable(obj: Any) -> Any:
    """
    将对象转换为可序列化的格式
    """
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
    """
    安全JSON序列化
    """
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def serialize_message(msg: Any) -> dict[str, Any]:
    """
    序列化消息
    """
    if isinstance(msg, SystemMessage):
        return {"type": "system", "content": msg.content}
    if isinstance(msg, HumanMessage):
        return {"type": "human", "content": msg.content}
    if isinstance(msg, ToolMessage):
        return {"type": "tool", "content": msg.content, "tool_call_id": msg.tool_call_id}
    if isinstance(msg, AIMessage):
        additional_kwargs = dict(getattr(msg, "additional_kwargs", {}) or {})
        response_metadata = getattr(msg, "response_metadata", {}) or {}
        if isinstance(response_metadata, dict):
            for key in ("reasoning_content", "reasoning"):
                if key in response_metadata and key not in additional_kwargs:
                    additional_kwargs[key] = response_metadata[key]
        return {
            "type": "ai",
            "content": msg.content,
            "tool_calls": getattr(msg, "tool_calls", []) or [],
            "additional_kwargs": additional_kwargs,
        }
    raise TypeError(f"unsupported message type: {type(msg).__name__}")


def deserialize_message(data: dict[str, Any]) -> Any:
    """
    反序列化消息
    """
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
    """
    归一化工具执行结果消息
    """
    if result.ok:
        return result.output or result.stdout or ""
    return result.error or result.stderr or "tool execution failed"
