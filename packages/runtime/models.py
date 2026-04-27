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
    事件类型（旧枚举，保留用于向后兼容；新代码请使用 EventKind）
    """
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
    MEMORY_RECALLED = "memory_recalled"
    MEMORY_STORED = "memory_stored"
    AUTOCOMPACT_APPLIED = "autocompact_applied"


class EventKind(str, enum.Enum):
    """
    新式事件类型，采用 scope.action 双段命名，供前端按前缀分发 reducer。
    """
    # ── model.* ─────── LLM 流式输出
    model_started         = "model.started"
    model_token           = "model.token"
    model_thinking        = "model.thinking"
    model_tool_call_delta = "model.tool_call_delta"
    model_completed       = "model.completed"
    model_usage           = "model.usage"

    # ── tool.* ─────── 工具执行全生命周期
    tool_pending_approval = "tool.pending_approval"
    tool_approved         = "tool.approved"
    tool_rejected         = "tool.rejected"
    tool_started          = "tool.started"
    tool_progress         = "tool.progress"
    tool_completed        = "tool.completed"
    tool_failed           = "tool.failed"
    tool_retried          = "tool.retried"
    tool_cached           = "tool.cached"

    # ── run.* ─────── agent run 生命周期
    run_started           = "run.started"
    run_resumed           = "run.resumed"
    run_waiting_human     = "run.waiting_human"
    run_token_budget      = "run.token_budget"
    run_completed         = "run.completed"
    run_degraded          = "run.degraded"
    run_failed            = "run.failed"
    run_cancelled         = "run.cancelled"
    run_forked            = "run.forked"

    # ── plan.* ─────── 工作流规划
    plan_created          = "plan.created"
    plan_approved         = "plan.approved"
    plan_step_started     = "plan.step_started"
    plan_step_completed   = "plan.step_completed"
    plan_step_failed      = "plan.step_failed"
    plan_replanned        = "plan.replanned"
    plan_completed        = "plan.completed"

    # ── memory.* ─────── 记忆系统
    memory_recalled       = "memory.recalled"
    memory_injected       = "memory.injected"
    memory_written        = "memory.written"
    memory_compacted      = "memory.compacted"
    memory_forgotten      = "memory.forgotten"

    # ── checkpoint.* ─────── 检查点
    checkpoint_saved      = "checkpoint.saved"
    checkpoint_restored   = "checkpoint.restored"

    # ── session.* ─────── 会话层面
    session_compacted       = "session.compacted"
    session_context_snipped = "session.context_snipped"


# 旧 EventType → 新 EventKind 映射表，供 EventBus 自动填充 event_kind 字段
_EVENTTYPE_TO_KIND: dict[EventType, EventKind] = {
    EventType.RUN_STARTED:          EventKind.run_started,
    EventType.RUN_RESUMED:          EventKind.run_resumed,
    EventType.STEP_STARTED:         EventKind.plan_step_started,
    EventType.MODEL_OUTPUT:         EventKind.model_completed,
    EventType.TOOL_SELECTED:        EventKind.tool_started,
    EventType.TOOL_STARTED:         EventKind.tool_started,
    EventType.TOOL_FINISHED:        EventKind.tool_completed,
    EventType.TOOL_FAILED:          EventKind.tool_failed,
    EventType.HUMAN_REQUIRED:       EventKind.tool_pending_approval,
    EventType.HUMAN_APPROVED:       EventKind.tool_approved,
    EventType.HUMAN_REJECTED:       EventKind.tool_rejected,
    EventType.STEP_FINISHED:        EventKind.plan_step_completed,
    EventType.CHECKPOINT_SAVED:     EventKind.checkpoint_saved,
    EventType.RUN_COMPLETED:        EventKind.run_completed,
    EventType.RUN_DEGRADED:         EventKind.run_degraded,
    EventType.RUN_FAILED:           EventKind.run_failed,
    EventType.CONTEXT_SNIPPED:      EventKind.session_context_snipped,
    EventType.MICROCOMPACT_APPLIED: EventKind.session_compacted,
    EventType.MEMORY_RECALLED:      EventKind.memory_recalled,
    EventType.MEMORY_STORED:        EventKind.memory_written,
    EventType.AUTOCOMPACT_APPLIED:  EventKind.memory_compacted,
}


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
    工具执行结果。
    新增渲染字段（A4）：render_kind / render_payload / truncated / artifact_id
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
    # ── A4: 前端差异化渲染字段 ──────────────────────────────────────────────
    # render_kind: text | diff | code | grep | web | terminal | todo | plan | json | error
    render_kind: str = "text"
    # render_payload 结构随 render_kind 变化，详见 UI_INTERACTION_SPEC.md 第二节
    render_payload: dict[str, Any] = field(default_factory=dict)
    # 输出超过 32 KB 落盘后置为 True，前端凭 artifact_id 按需拉取
    truncated: bool = False
    artifact_id: str | None = None


@dataclass(slots=True)
class AgentEvent:
    """
    agent事件信封。
    - event_type: 旧式枚举（向后兼容，由 EventBus 继续使用）
    - seq: 单调递增序号，由 EventBus.publish() 注入；用于 SSE Last-Event-ID 断线续传
    - event_kind: scope.action 格式的新式事件类型字符串；为空时由 EventBus 按映射表填充
    - scope / scope_id: 事件所属资源范围（run/plan_run/step_run/session）
    """
    run_id: str
    event_type: EventType
    ts: float
    step: int
    seq: int = 0
    event_kind: str = ""
    scope: str = "run"
    scope_id: str | None = None
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
