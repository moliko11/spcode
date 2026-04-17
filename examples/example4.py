from __future__ import annotations

import asyncio
import ast
import dataclasses
import enum
import fnmatch
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from packages.model_loader import create_model_loader


MODEL_URL = "http://10.8.160.47:9998/v1"
MODEL_NAME = "qwen3"
API_KEY = "EMPTY"
TEMPERATURE = 0.5

RUNTIME_DIR = Path("./runtime_data")
SESSION_DIR = RUNTIME_DIR / "sessions"
CHECKPOINT_DIR = RUNTIME_DIR / "checkpoints"
WORKSPACE_DIR = RUNTIME_DIR / "workspace"
AUDIT_LOG_PATH = RUNTIME_DIR / "audit.log"

import os
WORKSPACE_DIR = Path(os.getenv("AGENT_WORKSPACE", "./runtime_data/workspace"))

MAX_STEPS = 10
MAX_TOOL_CALLS = 6
MAX_SECONDS = 160
SHORT_MEMORY_TURNS = 8

DEFAULT_READ_MAX_BYTES = 64 * 1024
DEFAULT_SHELL_OUTPUT_LIMIT = 20_000


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("agent_runtime_example4")


class GuardrailViolation(Exception):
    """
    参数护栏异常，工具调用参数 参数异常。
    """
    pass


class PermissionDenied(Exception):
    """
    权限异常，工具调用权限不足。
    """
    pass


class BudgetExceeded(Exception):
    """
    预算异常，工具调用预算超限。
    """
    pass


class HumanInterventionRequired(Exception):
    """
    人工干预异常，工具调用需要人工干预。
    """
    def __init__(self, request: "HumanInterventionRequest") -> None:
        super().__init__(request.reason)
        self.request = request


class RunStatus(str, enum.Enum):
    """
    运行状态枚举。
    """
    IDLE = "idle"
    RUNNING = "running"
    TOOL_RUNNING = "tool_running"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Phase(str, enum.Enum):
    """
     运行阶段枚举。
    """
    DECIDING = "deciding"
    TOOL_PENDING = "tool_pending"
    TOOL_EXECUTED = "tool_executed"
    WAITING_HUMAN = "waiting_human"
    FINALIZING = "finalizing"
    COMPLETED = "completed"


class EventType(str, enum.Enum):
    """
    事件类型枚举。
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


@dataclass(slots=True)
class SessionMessage:
    """
    会话消息数据类。
    """
    role: str
    content: str
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class ToolCall:
    """
    工具调用数据类。
    """
    call_id: str # 工具调用ID
    tool_name: str # 工具名称
    arguments: dict[str, Any] # 工具调用参数
    idempotency_key: str # 工具调用ID，用于去重
    metadata: dict[str, Any] = field(default_factory=dict) # 元数据


@dataclass(slots=True)
class ToolResult:
    """
    工具调用结果数据类。
    """
    call_id: str # 工具调用ID
    tool_name: str # 工具名称
    ok: bool # 是否成功
    output: str | None = None # 工具调用输出
    error: str | None = None # 工具调用错误信息
    latency_ms: int = 0 # 工具调用耗时，单位毫秒
    metadata: dict[str, Any] = field(default_factory=dict) # 元数据
    stdout: str | None = None # 工具调用标准输出
    stderr: str | None = None # 工具调用标准错误
    exit_code: int | None = None # 工具调用退出码
    artifacts: list[dict[str, Any]] = field(default_factory=list) # 工具调用工件
    changed_files: list[str] = field(default_factory=list) # 工具调用修改的文件
    retry_count: int = 0 # 工具调用重试次数
    from_cache: bool = False # 是否从缓存中获取结果
    approved_by: str | None = None # 人工干预用户ID
    sandbox_mode: str | None = None # 工具调用沙箱模式


@dataclass(slots=True)
class AgentEvent:
    """
    代理事件数据类。
    """
    run_id: str # 运行ID
    event_type: EventType # 事件类型
    ts: float # 时间戳
    step: int # 步骤号
    payload: dict[str, Any] = field(default_factory=dict) # 事件负载


@dataclass(slots=True)
class HumanInterventionRequest:
    """
    人工干预请求数据类。
    """
    reason: str # 人工干预原因
    context: dict[str, Any] # 人工干预上下文
    suggested_actions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StepRecord:
    """
    步骤记录数据类。
    """
    step: int # 步骤号
    phase: str # 运行阶段
    raw_content: str # 原始模型输出
    raw_tool_calls: list[dict[str, Any]] # 原始工具调用
    model_name: str # 模型名称


@dataclass(slots=True)
class ToolSpec:
    name: str # 工具名称
    description: str # 工具描述
    parameters: dict[str, Any] # 工具参数
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
    命令行工具规格数据类。
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
    capture_output_limit: int = DEFAULT_SHELL_OUTPUT_LIMIT


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


def ensure_dirs() -> None:
    """
    确保运行时目录存在。
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def to_jsonable(obj: Any) -> Any:
    """
    将对象转换为可序列化的 JSON 格式。
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
    安全地将对象转换为 JSON 字符串，确保所有字符都可序列化。
    """
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def serialize_message(msg: Any) -> dict[str, Any]:
    """
    序列化消息为 JSON 字符串。
    """
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
    """
    反序列化 JSON 字符串为消息对象。
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


def workspace_resolve(path_str: str) -> Path:
    """
    解析工作空间路径，确保路径在工作空间内。
    """
    base = WORKSPACE_DIR.resolve()
    target = (WORKSPACE_DIR / path_str).resolve()
    if target != base and base not in target.parents:
        raise GuardrailViolation(f"path escapes workspace: {path_str}")
    return target


def truncate_text(text: str | None, limit: int) -> str | None:
    """
    截断文本，确保不超过指定长度。
    """
    if text is None or len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated]"


def snapshot_workspace(root: Path) -> dict[str, tuple[int, int]]:
    """
    创建工作空间快照，记录文件修改时间戳和大小。
    """
    snapshot: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return snapshot
    for path in root.rglob("*"):
        if path.is_file():
            stat = path.stat()
            snapshot[str(path.relative_to(WORKSPACE_DIR))] = (int(stat.st_mtime_ns), stat.st_size)
    return snapshot


def diff_workspace(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    """
    对比工作空间快照，返回修改的文件路径。
    """
    changed = set(before) ^ set(after)
    for path, stat in before.items():
        if path in after and after[path] != stat:
            changed.add(path)
    return sorted(changed)


def normalize_tool_message(result: ToolResult) -> str:
    """
    归一化工具调用结果，返回输出或错误信息。
    """
    if result.ok:
        return result.output or result.stdout or ""
    return result.error or result.stderr or "tool execution failed"


class EventSubscriber(Protocol):
    """
    事件订阅器协议，定义处理事件的方法。
    """
    async def handle(self, event: AgentEvent) -> None:
        ...


class EventBus:
    """
    事件总线，用于发布和订阅事件。
    """
    def __init__(self) -> None:
        self._subscribers: list[EventSubscriber] = []

    def subscribe(self, subscriber: EventSubscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    async def publish(self, event: AgentEvent) -> None:
        for subscriber in tuple(self._subscribers):
            try:
                await subscriber.handle(event)
            except Exception:
                logger.exception("event subscriber failed: %s", subscriber.__class__.__name__)


class LoggingSubscriber:
    """
    事件订阅器，将事件记录到日志。
    """
    async def handle(self, event: AgentEvent) -> None:
        logger.info(
            "event=%s run_id=%s step=%s payload=%s",
            event.event_type.value,
            event.run_id,
            event.step,
            event.payload,
        )


class AuditSubscriber:
    """
    事件订阅器，将工具调用事件记录到审计日志。
    """
    def __init__(self, path: Path = AUDIT_LOG_PATH) -> None:
        self.path = path

    async def handle(self, event: AgentEvent) -> None:
        if event.event_type not in {
            EventType.TOOL_STARTED,
            EventType.TOOL_FINISHED,
            EventType.TOOL_FAILED,
            EventType.HUMAN_REQUIRED,
            EventType.HUMAN_APPROVED,
            EventType.HUMAN_REJECTED,
        }:
            return
        record = {
            "ts": event.ts,
            "run_id": event.run_id,
            "step": event.step,
            "event_type": event.event_type.value,
            "payload": event.payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class FileSessionStore:
    """
    文件会话存储器，用于持久化会话消息。
    """
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def load_messages(self, session_id: str) -> list[SessionMessage]:
        path = self._path(session_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [SessionMessage(**item) for item in data]

    def append_message(self, session_id: str, role: str, content: str) -> None:
        messages = self.load_messages(session_id)
        messages.append(SessionMessage(role=role, content=content))
        self._path(session_id).write_text(
            json.dumps([to_jsonable(m) for m in messages], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class FileCheckpointStore:
    """
    文件检查点存储器，用于持久化检查点状态。
    """
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def save(self, state: AgentState) -> None:
        payload = to_jsonable(state)
        self._path(state.run_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, run_id: str) -> AgentState | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return AgentState(
            run_id=data["run_id"],
            user_id=data["user_id"],
            task=data["task"],
            session_id=data["session_id"],
            status=RunStatus(data["status"]),
            phase=Phase(data["phase"]),
            step=int(data["step"]),
            started_at=float(data["started_at"]),
            updated_at=float(data["updated_at"]),
            history=[StepRecord(**item) for item in data.get("history", [])],
            tool_results=[ToolResult(**item) for item in data.get("tool_results", [])],
            final_output=data.get("final_output"),
            failure_reason=data.get("failure_reason"),
            conversation=[SessionMessage(**item) for item in data.get("conversation", [])],
            runtime_messages=list(data.get("runtime_messages", [])),
            pending_tool_call=data.get("pending_tool_call"),
            pending_tool_result=data.get("pending_tool_result"),
            pending_human_request=data.get("pending_human_request"),
            metadata=dict(data.get("metadata", {})),
        )


class RetryPolicy:
    """
    重试策略，用于处理失败的异步操作。
    """
    def __init__(self, max_retries: int = 2, base_delay: float = 0.4) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.last_retry_count = 0

    async def run(
        self,
        func: Callable[[], Any],
        retryable: Callable[[Exception], bool] | None = None,
        max_retries: int | None = None,
    ) -> Any:
        last_error: Optional[Exception] = None
        retryable = retryable or (lambda exc: True)
        retries = self.max_retries if max_retries is None else max_retries
        self.last_retry_count = 0

        for attempt in range(retries + 1):
            try:
                self.last_retry_count = attempt
                return await func()
            except Exception as exc:
                last_error = exc
                if attempt >= retries or not retryable(exc):
                    break
                await asyncio.sleep(self.base_delay * (2**attempt))

        assert last_error is not None
        raise last_error


class IdempotencyStore:
    """
   幂等存储器
    """
    def __init__(self) -> None:
        self._results: dict[str, ToolResult] = {}

    def get(self, key: str) -> Optional[ToolResult]:
        return self._results.get(key)

    def set(self, key: str, result: ToolResult) -> None:
        self._results[key] = result

    def export_snapshot(self) -> dict[str, Any]:
        return {k: to_jsonable(v) for k, v in self._results.items()}

    def load_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._results = {k: ToolResult(**v) for k, v in snapshot.items()}


class PermissionController:
    """
    权限控制器，用于检查工具调用权限。
    """
    def __init__(self, role_getter: Callable[[str], str]) -> None:
        self._role_getter = role_getter

    def check_tool_permission(self, state: AgentState, spec: ToolSpec) -> None:
        role = self._role_getter(state.user_id)
        if spec.allowed_roles and role not in spec.allowed_roles:
            raise PermissionDenied(f"role '{role}' cannot access tool '{spec.name}'")


class ApprovalController:
    """
    审批控制器，用于检查工具调用是否需要人工干预。
    """
    def needs_approval(self, spec: ToolSpec, call: ToolCall) -> bool:
        if call.metadata.get("approved"):
            return False
        if spec.approval_policy == "never":
            return False
        if spec.approval_policy == "always":
            return True
        if spec.approval_policy == "on_write":
            return spec.writes_workspace
        if spec.approval_policy == "on_high_risk":
            return spec.risk_level in {"high", "critical"}
        raise ValueError(f"unsupported approval policy: {spec.approval_policy}")

    async def require_approval_if_needed(self, spec: ToolSpec, call: ToolCall) -> None:
        if not self.needs_approval(spec, call):
            return
        raise HumanInterventionRequired(
            HumanInterventionRequest(
                reason=f"tool '{spec.name}' requires approval",
                context={
                    "tool_name": spec.name,
                    "arguments": to_jsonable(call.arguments),
                    "risk_level": spec.risk_level,
                    "side_effect": spec.side_effect,
                    "reason": f"approval_policy={spec.approval_policy}",
                },
                suggested_actions=["approve", "reject", "edit_arguments"],
            )
        )


class GuardrailEngine:
    """
    安全引擎，用于验证用户输入和工具调用参数。
    """
    def validate_user_input(self, task: str) -> None:
        """
        验证用户输入是否包含敏感内容。
        """
        blocked = ["steal secrets", "delete production database"]
        lower_task = task.lower()
        for pattern in blocked:
            if pattern in lower_task:
                raise GuardrailViolation(f"blocked task pattern detected: {pattern}")

    def validate_tool_args(self, tool_name: str, arguments: dict[str, object]) -> None:
        if not isinstance(arguments, dict):
            raise GuardrailViolation(f"tool '{tool_name}' arguments must be a dict")

        if tool_name == "calculator":
            """
            验证计算器工具参数。
            """
            expression = arguments.get("expression")
            if not isinstance(expression, str) or not expression.strip():
                raise GuardrailViolation("calculator requires a non-empty expression")
        elif tool_name == "read_file":
            """
            验证读取文件工具参数。
            """
            self._validate_workspace_path(tool_name, arguments, require_content=False)
        elif tool_name == "write_note":
            """
            验证写入笔记工具参数。
            """
            self._validate_workspace_path(tool_name, arguments, require_content=True)
        elif tool_name == "list_dir":
            """
            验证列出目录工具参数。
            """
            path = arguments.get("path", ".")
            if not isinstance(path, str):
                raise GuardrailViolation("list_dir.path must be a string")
            workspace_resolve(path)
        elif tool_name == "glob_search":
            """
            验证全局搜索工具参数。
            """
            path = arguments.get("path", ".")
            pattern = arguments.get("pattern")
            if not isinstance(path, str):
                raise GuardrailViolation("glob_search.path must be a string")
            if not isinstance(pattern, str) or not pattern.strip():
                raise GuardrailViolation("glob_search.pattern must be a non-empty string")
            workspace_resolve(path)
        elif tool_name == "shell":
            """
            验证 shell 工具参数。
            """
            command = arguments.get("command")
            if not isinstance(command, str) or not command.strip():
                raise GuardrailViolation("shell.command must be a non-empty string")
            workdir = arguments.get("workdir", ".")
            if not isinstance(workdir, str):
                raise GuardrailViolation("shell.workdir must be a string")
            workspace_resolve(workdir)

    def validate_tool_result(self, result: ToolResult) -> None:
        if result.stdout and "\0" in result.stdout:
            raise GuardrailViolation("tool stdout contains binary data")
        if result.stderr and "\0" in result.stderr:
            raise GuardrailViolation("tool stderr contains binary data")

    def validate_final_output(self, output: str) -> None:
        if not output.strip():
            raise GuardrailViolation("final output must not be empty")

    def _validate_workspace_path(
        self,
        tool_name: str,
        arguments: dict[str, object],
        require_content: bool,
    ) -> None:
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            raise GuardrailViolation(f"{tool_name}.path must be a non-empty string")
        workspace_resolve(path)
        if require_content:
            content = arguments.get("content")
            if not isinstance(content, str):
                raise GuardrailViolation(f"{tool_name}.content must be a string")


class BaseTool(Protocol):
    async def arun(self, arguments: dict[str, Any]) -> str | ToolResult:
        ...


class ToolRegistry:
    """
    工具注册器，用于注册和获取工具。
    """
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._tools: dict[str, BaseTool] = {}

    def register(self, spec: ToolSpec, tool: BaseTool) -> None:
        self._specs[spec.name] = spec
        self._tools[spec.name] = tool

    def get_spec(self, name: str) -> ToolSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise ValueError(f"unknown tool spec: {name}")
        return spec

    def get_tool(self, name: str) -> BaseTool:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool implementation: {name}")
        return tool

    def openai_tools(self) -> list[dict[str, Any]]:
        tools = []
        for spec in self._specs.values():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
            )
        return tools


class GetCurrentTimeTool:
    async def arun(self, arguments: dict[str, Any]) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class CalculatorTool:
    async def arun(self, arguments: dict[str, Any]) -> str:
        expression = arguments["expression"]
        tree = ast.parse(expression, mode="eval")
        allowed_nodes = (
            ast.Expression,
            ast.BinOp,
            ast.UnaryOp,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.Pow,
            ast.UAdd,
            ast.USub,
            ast.Constant,
        )
        for node in ast.walk(tree):
            if not isinstance(node, allowed_nodes):
                raise ValueError(f"unsupported node: {type(node).__name__}")
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                raise ValueError("only numeric constants are allowed")
        value = eval(compile(tree, filename="<expr>", mode="eval"), {"__builtins__": {}}, {})
        return str(value)


class ReadFileTool:
    def __init__(self, max_bytes: int = DEFAULT_READ_MAX_BYTES) -> None:
        self.max_bytes = max_bytes

    async def arun(self, arguments: dict[str, Any]) -> str:
        path = workspace_resolve(arguments["path"])
        if not path.exists():
            raise FileNotFoundError(f"file not found: {path.name}")
        if path.is_dir():
            raise IsADirectoryError(f"cannot read a directory: {path.name}")
        data = path.read_bytes()
        if len(data) > self.max_bytes:
            data = data[: self.max_bytes]
        if b"\0" in data:
            raise ValueError("binary files are not supported")
        return data.decode("utf-8")


class WriteNoteTool:
    async def arun(self, arguments: dict[str, Any]) -> str:
        path = workspace_resolve(arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"], encoding="utf-8")
        return f"wrote file: {path.relative_to(WORKSPACE_DIR)}"


class ListDirTool:
    async def arun(self, arguments: dict[str, Any]) -> str:
        root = workspace_resolve(arguments.get("path", "."))
        if not root.exists():
            raise FileNotFoundError(f"directory not found: {root.name}")
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {root.name}")
        entries = []
        for child in sorted(root.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            kind = "dir" if child.is_dir() else "file"
            entries.append(f"{kind}\t{child.relative_to(WORKSPACE_DIR)}")
        return "\n".join(entries)


class GlobSearchTool:
    """
    递归搜索工具，用于在指定路径下递归搜索匹配模式的文件。
    """
    async def arun(self, arguments: dict[str, Any]) -> str:
        root = workspace_resolve(arguments.get("path", "."))
        pattern = arguments["pattern"]
        matches = []
        for path in root.rglob("*"):
            rel = str(path.relative_to(WORKSPACE_DIR))
            if fnmatch.fnmatch(rel, pattern):
                matches.append(rel)
        return "\n".join(sorted(matches))


class ShellExecutor:
    """
    命令行工具执行器，用于执行系统命令。
    """
    def __init__(self, workspace_dir: Path = WORKSPACE_DIR) -> None:
        self.workspace_dir = workspace_dir

    async def run(self, spec: ShellToolSpec, arguments: dict[str, Any]) -> ToolResult:
        command = arguments["command"]
        workdir = self._resolve_workdir(spec, arguments.get("workdir", "."))
        self._check_command_allowed(spec, command)

        before = snapshot_workspace(WORKSPACE_DIR)
        env = self._build_env(spec)

        process = await asyncio.create_subprocess_exec(
            *self._build_command(spec.shell_mode, command),
            cwd=str(workdir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(process.communicate(), timeout=spec.timeout_s)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise TimeoutError(f"shell command timed out after {spec.timeout_s}s") from exc

        stdout = truncate_text(stdout_raw.decode("utf-8", errors="replace"), spec.capture_output_limit)
        stderr = truncate_text(stderr_raw.decode("utf-8", errors="replace"), spec.capture_output_limit)
        after = snapshot_workspace(WORKSPACE_DIR)
        changed_files = diff_workspace(before, after)

        ok = process.returncode == 0
        output = (stdout or "").strip() if ok else None
        error = None
        if not ok:
            error = (stderr or stdout or f"shell exited with code {process.returncode}").strip()

        return ToolResult(
            call_id="",
            tool_name="shell",
            ok=ok,
            output=output,
            error=error,
            stdout=stdout,
            stderr=stderr,
            exit_code=process.returncode,
            changed_files=changed_files,
            sandbox_mode=spec.working_dir_mode,
            metadata={"command": command, "workdir": str(workdir.relative_to(WORKSPACE_DIR))},
        )

    def _resolve_workdir(self, spec: ShellToolSpec, workdir: str) -> Path:
        base = WORKSPACE_DIR.resolve()
        resolved = workspace_resolve(workdir)
        if spec.working_dir_mode == "workspace_only" and resolved != base and base not in resolved.parents:
            raise GuardrailViolation("shell workdir must stay inside workspace")
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _check_command_allowed(self, spec: ShellToolSpec, command: str) -> None:
        if spec.allowed_commands:
            prefix = command.strip().split()[0]
            if prefix not in spec.allowed_commands:
                raise GuardrailViolation(f"shell command '{prefix}' is not in the allowlist")
        for pattern in spec.blocked_patterns:
            if re.search(pattern, command, flags=re.IGNORECASE):
                raise GuardrailViolation(f"shell command blocked by pattern: {pattern}")

    def _build_env(self, spec: ShellToolSpec) -> dict[str, str]:
        if spec.env_policy == "empty":
            return {}
        if spec.env_policy == "safe_default":
            allowed = ["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"]
            return {key: value for key, value in os.environ.items() if key.upper() in allowed}
        if spec.env_policy == "inherit_filtered":
            blocked = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
            return {key: value for key, value in os.environ.items() if key not in blocked}
        raise ValueError(f"unsupported env policy: {spec.env_policy}")

    def _build_command(self, shell_mode: str, command: str) -> list[str]:
        if shell_mode == "powershell":
            return ["powershell", "-NoProfile", "-Command", command]
        if shell_mode == "bash":
            return ["bash", "-lc", command]
        if shell_mode == "sh":
            return ["sh", "-lc", command]
        raise ValueError(f"unsupported shell mode: {shell_mode}")


class ShellTool:
    """
    命令行工具工具，用于执行系统命令。
    """
    def __init__(self, executor: ShellExecutor, spec_getter: Callable[[], ShellToolSpec]) -> None:
        self.executor = executor
        self.spec_getter = spec_getter

    async def arun(self, arguments: dict[str, Any]) -> ToolResult:
        return await self.executor.run(self.spec_getter(), arguments)


class ToolExecutor:
    """
    工具执行器，用于执行工具调用。
    """
    def __init__(
        self,
        registry: ToolRegistry,
        permission_controller: PermissionController,
        approval_controller: ApprovalController,
        guardrail_engine: GuardrailEngine,
        retry_policy: RetryPolicy,
        idempotency_store: IdempotencyStore,
        event_bus: EventBus,
    ) -> None:
        self.registry = registry
        self.permission_controller = permission_controller
        self.approval_controller = approval_controller
        self.guardrail_engine = guardrail_engine
        self.retry_policy = retry_policy
        self.idempotency_store = idempotency_store
        self.event_bus = event_bus

    async def execute(self, state: AgentState, call: ToolCall) -> ToolResult:
        spec = self.registry.get_spec(call.tool_name)
        tool = self.registry.get_tool(call.tool_name)

        self.guardrail_engine.validate_tool_args(call.tool_name, call.arguments)
        self.permission_controller.check_tool_permission(state, spec)
        await self.approval_controller.require_approval_if_needed(spec, call)

        if call.idempotency_key and spec.cache_policy != "none":
            cached = self.idempotency_store.get(call.idempotency_key)
            if cached is not None:
                return dataclasses.replace(cached, from_cache=True)

        sandbox_mode = (
            spec.working_dir_mode if isinstance(spec, ShellToolSpec) else ("workspace_only" if spec.sandbox_required else None)
        )
        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.TOOL_STARTED,
                ts=time.time(),
                step=state.step,
                payload={
                    "tool_name": call.tool_name,
                    "arguments": call.arguments,
                    "idempotency_key": call.idempotency_key,
                    "risk_level": spec.risk_level,
                    "side_effect": spec.side_effect,
                    "sandbox_mode": sandbox_mode,
                },
            )
        )

        async def _invoke_once() -> ToolResult:
            raw = await asyncio.wait_for(tool.arun(call.arguments), timeout=spec.timeout_s)
            result = self._normalize_result(call, raw)
            result.approved_by = call.metadata.get("approved_by")
            result.sandbox_mode = result.sandbox_mode or sandbox_mode
            self.guardrail_engine.validate_tool_result(result)
            return result

        def _retryable(exc: Exception) -> bool:
            return not isinstance(
                exc,
                (GuardrailViolation, PermissionDenied, HumanInterventionRequired, ValueError),
            )

        start = time.time()
        try:
            result = await self.retry_policy.run(
                _invoke_once,
                retryable=_retryable,
                max_retries=spec.max_retries,
            )
            result.latency_ms = int((time.time() - start) * 1000)
            result.retry_count = self.retry_policy.last_retry_count
        except Exception as exc:
            result = ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=False,
                error=str(exc),
                latency_ms=int((time.time() - start) * 1000),
                metadata={"error_type": type(exc).__name__},
                retry_count=self.retry_policy.last_retry_count,
                approved_by=call.metadata.get("approved_by"),
                sandbox_mode=sandbox_mode,
            )

        self._cache_result(spec, call, result)

        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.TOOL_FINISHED if result.ok else EventType.TOOL_FAILED,
                ts=time.time(),
                step=state.step,
                payload={
                    "tool_name": call.tool_name,
                    "ok": result.ok,
                    "risk_level": spec.risk_level,
                    "side_effect": spec.side_effect,
                    "from_cache": result.from_cache,
                    "retry_count": result.retry_count,
                    "latency_ms": result.latency_ms,
                    "error": result.error,
                },
            )
        )
        return result

    def _normalize_result(self, call: ToolCall, raw: str | ToolResult) -> ToolResult:
        if isinstance(raw, ToolResult):
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=raw.ok,
                output=raw.output,
                error=raw.error,
                latency_ms=raw.latency_ms,
                metadata=dict(raw.metadata),
                stdout=raw.stdout,
                stderr=raw.stderr,
                exit_code=raw.exit_code,
                artifacts=list(raw.artifacts),
                changed_files=list(raw.changed_files),
                retry_count=raw.retry_count,
                from_cache=raw.from_cache,
                approved_by=raw.approved_by,
                sandbox_mode=raw.sandbox_mode,
            )
        return ToolResult(call_id=call.call_id, tool_name=call.tool_name, ok=True, output=str(raw))

    def _cache_result(self, spec: ToolSpec, call: ToolCall, result: ToolResult) -> None:
        if not call.idempotency_key or spec.cache_policy == "none":
            return
        if spec.cache_policy == "success_only" and not result.ok:
            return
        self.idempotency_store.set(call.idempotency_key, result)


class NativeToolCallingLLMClient:
    """
    原生工具调用 LLM 客户端，用于与支持原生工具调用的 LLM 进行交互。
    """
    def __init__(self, llm: Any, model_name: str, tool_schemas: list[dict[str, Any]]) -> None:
        self.model_name = model_name
        self.raw_llm = llm
        self.bound_llm = self._bind_tools(llm, tool_schemas)

    def _bind_tools(self, llm: Any, tool_schemas: list[dict[str, Any]]) -> Any:
        if hasattr(llm, "bind_tools"):
            return llm.bind_tools(tool_schemas)
        raise RuntimeError("current llm object does not support bind_tools")

    async def invoke(self, messages: list[Any]) -> Any:
        if hasattr(self.bound_llm, "ainvoke"):
            return await self.bound_llm.ainvoke(messages)
        return await asyncio.to_thread(self.bound_llm.invoke, messages)

    def extract_content_and_tool_calls(self, response: Any) -> tuple[str, list[dict[str, Any]]]:
        """
        从 LLM 响应中提取内容和工具调用。
        """
        content = self._coerce_content(getattr(response, "content", ""))
        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            normalized = []
            for item in tool_calls:
                normalized.append(
                    {
                        "id": item.get("id") or str(uuid.uuid4()),
                        "name": item.get("name"),
                        "arguments": item.get("args", {}) or {},
                    }
                )
            return content, normalized

        additional_kwargs = getattr(response, "additional_kwargs", {}) or {}
        raw_tool_calls = additional_kwargs.get("tool_calls", []) or []
        if raw_tool_calls:
            normalized = []
            for item in raw_tool_calls:
                fn = item.get("function", {}) or {}
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                normalized.append(
                    {
                        "id": item.get("id") or str(uuid.uuid4()),
                        "name": fn.get("name"),
                        "arguments": args if isinstance(args, dict) else {},
                    }
                )
            return content, normalized
        return content, []

    @staticmethod
    def _coerce_content(content: Any) -> str:
        """
        将 LLM 响应中的内容转换为字符串。
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts).strip()
        return str(content)


class MessageBuilder:
    """
    消息构建器，用于构建初始消息序列。
    """
    def __init__(self, short_memory_turns: int = SHORT_MEMORY_TURNS) -> None:
        self.short_memory_turns = short_memory_turns

    def build_initial_messages(self, state: AgentState) -> list[Any]:
        recent = state.conversation[-self.short_memory_turns :]
        messages: list[Any] = [
            SystemMessage(
                content=(
                    "You are a concise assistant.\n"
                    "Use tools only when needed.\n"
                    "Do not fabricate tool results.\n"
                    "Available tools include calculator, time, file read/write, list_dir, glob_search, and shell.\n"
                    "Writing files and shell commands may require approval.\n"
                )
            )
        ]
        for msg in recent:
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                messages.append(AIMessage(content=msg.content))
        return messages


class AgentRuntime:
    """
    代理运行时，用于管理代理的运行流程。
    """
    def __init__(
        self,
        llm_client: NativeToolCallingLLMClient,
        message_builder: MessageBuilder,
        tool_executor: ToolExecutor,
        registry: ToolRegistry,
        session_store: FileSessionStore,
        checkpoint_store: FileCheckpointStore,
        event_bus: EventBus,
        guardrail_engine: GuardrailEngine,
        budget_controller: "BudgetController",
        idempotency_store: IdempotencyStore,
    ) -> None:
        self.failpoint: str | None = None
        self.llm_client = llm_client
        self.message_builder = message_builder
        self.tool_executor = tool_executor
        self.registry = registry
        self.session_store = session_store
        self.checkpoint_store = checkpoint_store
        self.event_bus = event_bus
        self.guardrail_engine = guardrail_engine
        self.budget_controller = budget_controller
        self.idempotency_store = idempotency_store

    def set_failpoint(self, name: str | None) -> None:
        self.failpoint = name

    def _hit_failpoint(self, name: str) -> None:
        if self.failpoint == name:
            raise RuntimeError(f"injected failpoint: {name}")

    async def chat(self, user_id: str, session_id: str, message: str) -> AgentState:
        """
        处理用户输入，与 LLM 进行对话。
        """
        self.guardrail_engine.validate_user_input(message)
        previous = self.session_store.load_messages(session_id)
        self.session_store.append_message(session_id, "user", message)

        state = AgentState(
            run_id=str(uuid.uuid4()),
            user_id=user_id,
            task=message,
            session_id=session_id,
            status=RunStatus.RUNNING,
            phase=Phase.DECIDING,
            conversation=previous + [SessionMessage(role="user", content=message)],
            metadata={"tool_ledger": {}},
        )

        runtime_messages = self.message_builder.build_initial_messages(state)
        state.runtime_messages = [serialize_message(m) for m in runtime_messages]

        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.RUN_STARTED,
                ts=time.time(),
                step=state.step,
                payload={"task": message, "session_id": session_id},
            )
        )
        await self._save_checkpoint(state)
        return await self._continue(state)

    async def restore(self, run_id: str) -> AgentState:
        state = self.checkpoint_store.load(run_id)
        if state is None:
            raise ValueError(f"checkpoint not found: {run_id}")
        snapshot = state.metadata.get("tool_ledger", {})
        if isinstance(snapshot, dict):
            self.idempotency_store.load_snapshot(snapshot)
        return state

    async def resume(self, run_id: str, human_decision: dict[str, Any] | None = None) -> AgentState:
        state = await self.restore(run_id)
        if state.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return state

        if state.phase == Phase.WAITING_HUMAN:
            if human_decision is None:
                raise ValueError("human_decision is required while waiting for approval")

            approved = bool(human_decision.get("approved", False))
            edited_arguments = human_decision.get("edited_arguments")
            approved_by = str(human_decision.get("approved_by", "human"))

            if not approved:
                state.status = RunStatus.COMPLETED
                state.phase = Phase.COMPLETED
                state.final_output = "human approval rejected; execution stopped"
                state.pending_human_request = None
                await self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.HUMAN_REJECTED,
                        ts=time.time(),
                        step=state.step,
                        payload={"reason": "approval rejected"},
                    )
                )
                await self._finalize(state)
                return state

            if state.pending_tool_call is None:
                raise ValueError("missing pending_tool_call while waiting for human input")

            if edited_arguments is not None:
                if not isinstance(edited_arguments, dict):
                    raise ValueError("edited_arguments must be a dict")
                self.guardrail_engine.validate_tool_args(state.pending_tool_call["tool_name"], edited_arguments)
                state.pending_tool_call["arguments"] = edited_arguments

            state.pending_tool_call.setdefault("metadata", {})
            state.pending_tool_call["metadata"]["approved"] = True
            state.pending_tool_call["metadata"]["approved_by"] = approved_by
            state.pending_human_request = None
            state.status = RunStatus.RUNNING
            state.phase = Phase.TOOL_PENDING

            await self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.HUMAN_APPROVED,
                    ts=time.time(),
                    step=state.step,
                    payload={"tool_name": state.pending_tool_call["tool_name"], "approved_by": approved_by},
                )
            )

        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.RUN_RESUMED,
                ts=time.time(),
                step=state.step,
                payload={"phase": state.phase.value},
            )
        )
        await self._save_checkpoint(state)
        return await self._continue(state)

    async def _continue(self, state: AgentState) -> AgentState:
        while True:
            state.updated_at = time.time()
            self.budget_controller.check(state)
            runtime_messages = [deserialize_message(m) for m in state.runtime_messages]

            if state.phase == Phase.DECIDING:
                await self.event_bus.publish(
                    AgentEvent(run_id=state.run_id, event_type=EventType.STEP_STARTED, ts=time.time(), step=state.step)
                )
                response = await self.llm_client.invoke(runtime_messages)
                content, tool_calls = self.llm_client.extract_content_and_tool_calls(response)

                await self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.MODEL_OUTPUT,
                        ts=time.time(),
                        step=state.step,
                        payload={"content": content, "tool_calls": tool_calls},
                    )
                )

                state.history.append(
                    StepRecord(
                        step=state.step,
                        phase=state.phase.value,
                        raw_content=content,
                        raw_tool_calls=tool_calls,
                        model_name=self.llm_client.model_name,
                    )
                )

                runtime_messages.append(response)
                state.runtime_messages = [serialize_message(m) for m in runtime_messages]
                await self._save_checkpoint(state)

                if not tool_calls:
                    answer = (content or "").strip() or "no valid answer was produced"
                    self.guardrail_engine.validate_final_output(answer)
                    state.final_output = answer
                    state.status = RunStatus.COMPLETED
                    state.phase = Phase.COMPLETED
                    await self._finalize(state)
                    return state

                raw_call = tool_calls[0]
                call = ToolCall(
                    call_id=raw_call["id"],
                    tool_name=raw_call["name"],
                    arguments=raw_call["arguments"],
                    idempotency_key=f"{state.run_id}:{raw_call['name']}:{safe_json_dumps(raw_call['arguments'])}",
                )
                state.pending_tool_call = to_jsonable(call)
                state.phase = Phase.TOOL_PENDING

                await self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.TOOL_SELECTED,
                        ts=time.time(),
                        step=state.step,
                        payload={"tool_name": call.tool_name, "arguments": call.arguments},
                    )
                )
                await self._save_checkpoint(state)
                continue

            if state.phase == Phase.TOOL_PENDING:
                if state.pending_tool_call is None:
                    raise RuntimeError("TOOL_PENDING without pending_tool_call")
                call = ToolCall(**state.pending_tool_call)
                state.status = RunStatus.TOOL_RUNNING

                try:
                    self._hit_failpoint("before_tool_execute")
                    result = await self.tool_executor.execute(state, call)
                except HumanInterventionRequired as exc:
                    state.status = RunStatus.WAITING_HUMAN
                    state.phase = Phase.WAITING_HUMAN
                    state.pending_human_request = to_jsonable(exc.request)
                    await self.event_bus.publish(
                        AgentEvent(
                            run_id=state.run_id,
                            event_type=EventType.HUMAN_REQUIRED,
                            ts=time.time(),
                            step=state.step,
                            payload={"reason": exc.request.reason, "tool_name": call.tool_name},
                        )
                    )
                    await self._save_checkpoint(state)
                    return state

                state.status = RunStatus.RUNNING
                state.tool_results.append(result)
                state.pending_tool_result = to_jsonable(result)
                state.metadata["tool_ledger"] = self.idempotency_store.export_snapshot()
                state.phase = Phase.TOOL_EXECUTED
                await self._save_checkpoint(state)
                self._hit_failpoint("after_tool_executed_checkpoint")
                continue

            if state.phase == Phase.TOOL_EXECUTED:
                if state.pending_tool_call is None or state.pending_tool_result is None:
                    raise RuntimeError("TOOL_EXECUTED missing pending state")
                call = ToolCall(**state.pending_tool_call)
                result = ToolResult(**state.pending_tool_result)
                runtime_messages.append(ToolMessage(content=normalize_tool_message(result), tool_call_id=call.call_id))
                state.runtime_messages = [serialize_message(m) for m in runtime_messages]
                state.pending_tool_call = None
                state.pending_tool_result = None
                state.phase = Phase.DECIDING
                state.step += 1

                await self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.STEP_FINISHED,
                        ts=time.time(),
                        step=state.step,
                        payload={"tool_name": call.tool_name, "ok": result.ok},
                    )
                )
                await self._save_checkpoint(state)
                continue

            if state.phase in {Phase.WAITING_HUMAN, Phase.COMPLETED}:
                return state
            raise RuntimeError(f"unknown phase: {state.phase}")

    async def _save_checkpoint(self, state: AgentState) -> None:
        self.checkpoint_store.save(state)
        await self.event_bus.publish(
            AgentEvent(run_id=state.run_id, event_type=EventType.CHECKPOINT_SAVED, ts=time.time(), step=state.step)
        )

    async def _finalize(self, state: AgentState) -> None:
        await self._save_checkpoint(state)
        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.RUN_COMPLETED,
                ts=time.time(),
                step=state.step,
                payload={"final_output": state.final_output},
            )
        )
        self.session_store.append_message(state.session_id, "assistant", state.final_output or "")


class BudgetController:
    """
    预算控制器，用于限制代理的运行时间。
    """
    def __init__(self, max_steps: int, max_tool_calls: int, max_seconds: int) -> None:
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.max_seconds = max_seconds

    def check(self, state: AgentState) -> None:
        if state.step >= self.max_steps:
            raise BudgetExceeded(f"max steps exceeded: {self.max_steps}")
        if len(state.tool_results) >= self.max_tool_calls:
            raise BudgetExceeded(f"max tool calls exceeded: {self.max_tool_calls}")
        if time.time() - state.started_at >= self.max_seconds:
            raise BudgetExceeded(f"max runtime exceeded: {self.max_seconds}s")


def build_runtime() -> AgentRuntime:
    ensure_dirs()
    loader = create_model_loader(
        model_url=MODEL_URL,
        model_name=MODEL_NAME,
        api_key=API_KEY,
        temperature=TEMPERATURE,
    )
    llm = loader.load()

    registry = ToolRegistry()
    shell_spec = ShellToolSpec(
        name="shell",
        description="Run a shell command inside the workspace after approval.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "workdir": {"type": "string", "description": "Relative workspace directory", "default": "."},
            },
            "required": ["command"],
        },
        timeout_s=10.0,
        readonly=False,
        risk_level="high",
        side_effect="shell",
        sandbox_required=True,
        writes_workspace=True,
        cache_policy="none",
        max_retries=0,
        approval_policy="always",
    )

    registry.register(
        ToolSpec(
            name="get_current_time",
            description="Get current local time.",
            parameters={"type": "object", "properties": {}, "required": []},
            readonly=True,
            risk_level="low",
            side_effect="none",
            cache_policy="success_only",
        ),
        GetCurrentTimeTool(),
    )
    registry.register(
        ToolSpec(
            name="calculator",
            description="Evaluate a basic math expression.",
            parameters={
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "Math expression"}},
                "required": ["expression"],
            },
            readonly=True,
            risk_level="low",
            side_effect="none",
            cache_policy="success_only",
        ),
        CalculatorTool(),
    )
    registry.register(
        ToolSpec(
            name="read_file",
            description="Read a UTF-8 text file from workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative workspace path"}},
                "required": ["path"],
            },
            readonly=True,
            risk_level="medium",
            side_effect="local_fs",
            sandbox_required=True,
            cache_policy="success_only",
        ),
        ReadFileTool(),
    )
    registry.register(
        ToolSpec(
            name="write_note",
            description="Write a text file in the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative workspace path"},
                    "content": {"type": "string", "description": "File content"},
                },
                "required": ["path", "content"],
            },
            readonly=False,
            risk_level="high",
            side_effect="local_fs",
            sandbox_required=True,
            writes_workspace=True,
            cache_policy="success_only",
            max_retries=0,
            approval_policy="always",
        ),
        WriteNoteTool(),
    )
    registry.register(
        ToolSpec(
            name="list_dir",
            description="List files and directories inside workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative directory", "default": "."}},
                "required": [],
            },
            readonly=True,
            risk_level="medium",
            side_effect="local_fs",
            sandbox_required=True,
            cache_policy="success_only",
        ),
        ListDirTool(),
    )
    registry.register(
        ToolSpec(
            name="glob_search",
            description="Find files by glob pattern inside workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative directory", "default": "."},
                    "pattern": {"type": "string", "description": "Glob pattern"},
                },
                "required": ["pattern"],
            },
            readonly=True,
            risk_level="medium",
            side_effect="local_fs",
            sandbox_required=True,
            cache_policy="success_only",
        ),
        GlobSearchTool(),
    )
    registry.register(shell_spec, ShellTool(ShellExecutor(WORKSPACE_DIR), lambda: shell_spec))

    event_bus = EventBus()
    event_bus.subscribe(LoggingSubscriber())
    event_bus.subscribe(AuditSubscriber())

    guardrail_engine = GuardrailEngine()
    idempotency_store = IdempotencyStore()
    role_getter = lambda user_id: "user"

    tool_executor = ToolExecutor(
        registry=registry,
        permission_controller=PermissionController(role_getter),
        approval_controller=ApprovalController(),
        guardrail_engine=guardrail_engine,
        retry_policy=RetryPolicy(max_retries=2, base_delay=0.3),
        idempotency_store=idempotency_store,
        event_bus=event_bus,
    )

    llm_client = NativeToolCallingLLMClient(
        llm=llm,
        model_name=MODEL_NAME,
        tool_schemas=registry.openai_tools(),
    )

    return AgentRuntime(
        llm_client=llm_client,
        message_builder=MessageBuilder(short_memory_turns=SHORT_MEMORY_TURNS),
        tool_executor=tool_executor,
        registry=registry,
        session_store=FileSessionStore(SESSION_DIR),
        checkpoint_store=FileCheckpointStore(CHECKPOINT_DIR),
        event_bus=event_bus,
        guardrail_engine=guardrail_engine,
        budget_controller=BudgetController(
            max_steps=MAX_STEPS,
            max_tool_calls=MAX_TOOL_CALLS,
            max_seconds=MAX_SECONDS,
        ),
        idempotency_store=idempotency_store,
    )


HELP = """
Commands:
1. Chat directly, for example:
   - 88+666 equals what?
   - what time is it
   - read notes/demo.txt
   - write a note to notes/today.txt
2. /resume <run_id>
3. /approve <run_id>
4. /reject <run_id>
5. /edit <run_id> {"path":"hello.py","content":"print('hi')\\n"}
6. exit
""".strip()


def format_pending_human_request(state: AgentState) -> str:
    if not state.pending_human_request:
        return "No pending human approval request."
    request = state.pending_human_request
    context = request.get("context", {})
    lines = [
        "Approval required:",
        f"- reason: {request.get('reason')}",
        f"- tool: {context.get('tool_name')}",
        f"- risk: {context.get('risk_level')}",
        f"- side_effect: {context.get('side_effect')}",
        f"- arguments: {json.dumps(context.get('arguments', {}), ensure_ascii=False)}",
    ]
    suggested = request.get("suggested_actions") or []
    if suggested:
        lines.append(f"- actions: {', '.join(suggested)}")
    return "\n".join(lines)


def parse_edit_json(raw_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON for edited arguments: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("edited arguments must be a JSON object")
    return payload


async def handle_cli_approval_interaction(runtime: AgentRuntime, state: AgentState) -> AgentState:
    if state.status != RunStatus.WAITING_HUMAN:
        return state

    print(format_pending_human_request(state))
    print("Approval actions: [a]pprove  [r]eject  [e]dit arguments then approve  [s]kip")

    while True:
        try:
            choice = input("Approval> ").strip().lower()
        except EOFError:
            print("No interactive approval input is available in the current runtime.")
            print(f"Resume later with /approve {state.run_id}, /reject {state.run_id}, or /edit {state.run_id} <json>.")
            return state
        if choice in {"", "s", "skip"}:
            print(f"Pending approval preserved. Resume later with /approve {state.run_id} or /reject {state.run_id}.")
            return state
        if choice in {"a", "approve"}:
            return await runtime.resume(state.run_id, human_decision={"approved": True, "approved_by": "cli"})
        if choice in {"r", "reject"}:
            return await runtime.resume(state.run_id, human_decision={"approved": False, "approved_by": "cli"})
        if choice in {"e", "edit"}:
            print("Enter edited arguments as JSON on one line.")
            print(f"Current arguments: {json.dumps(state.pending_tool_call['arguments'], ensure_ascii=False)}")
            try:
                raw_json = input("EditedArgs> ").strip()
            except EOFError:
                print("No interactive input is available for edited arguments.")
                print(f"Resume later with /edit {state.run_id} <json>.")
                return state
            try:
                edited_arguments = parse_edit_json(raw_json)
            except ValueError as exc:
                print(f"Invalid edited arguments: {exc}")
                continue
            return await runtime.resume(
                state.run_id,
                human_decision={
                    "approved": True,
                    "approved_by": "cli",
                    "edited_arguments": edited_arguments,
                },
            )
        print("Unknown approval action. Use approve, reject, edit, or skip.")


async def main() -> None:
    runtime = build_runtime()
    user_id = "demo_user_1"
    session_id = "demo_session_1"

    print("example4 runtime started")
    print(HELP)
    print(f"workspace: {WORKSPACE_DIR.resolve()}")

    while True:
        user_input = input("\nUser> ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue

        if user_input.startswith("/resume "):
            state = await runtime.resume(user_input.split(" ", 1)[1].strip())
        elif user_input.startswith("/approve "):
            state = await runtime.resume(
                user_input.split(" ", 1)[1].strip(),
                human_decision={"approved": True, "approved_by": "cli"},
            )
        elif user_input.startswith("/reject "):
            state = await runtime.resume(
                user_input.split(" ", 1)[1].strip(),
                human_decision={"approved": False, "approved_by": "cli"},
            )
        elif user_input.startswith("/edit "):
            rest = user_input.split(" ", 1)[1].strip()
            run_id, _, raw_json = rest.partition(" ")
            if not run_id or not raw_json:
                print("Usage: /edit <run_id> <json>")
                continue
            try:
                edited_arguments = parse_edit_json(raw_json)
            except ValueError as exc:
                print(f"Invalid edited arguments: {exc}")
                continue
            state = await runtime.resume(
                run_id,
                human_decision={
                    "approved": True,
                    "approved_by": "cli",
                    "edited_arguments": edited_arguments,
                },
            )
        else:
            state = await runtime.chat(user_id=user_id, session_id=session_id, message=user_input)

        if state.status == RunStatus.WAITING_HUMAN:
            state = await handle_cli_approval_interaction(runtime, state)

        print(f"\nrun_id: {state.run_id}")
        print(f"status: {state.status.value}")
        print(f"phase: {state.phase.value}")
        print(f"answer: {state.final_output or state.failure_reason}")


if __name__ == "__main__":
    asyncio.run(main())
