from __future__ import annotations

import asyncio
import ast
import dataclasses
import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from packages.model_loader import create_model_loader
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


# =========================
# 配置
# =========================

MODEL_URL = "http://10.8.160.47:9998/v1"
MODEL_NAME = "qwen3"
API_KEY = "EMPTY"
TEMPERATURE = 0.5

RUNTIME_DIR = Path("./runtime_data")
SESSION_DIR = RUNTIME_DIR / "sessions"
CHECKPOINT_DIR = RUNTIME_DIR / "checkpoints"
WORKSPACE_DIR = RUNTIME_DIR / "workspace"

MAX_STEPS = 10
MAX_TOOL_CALLS = 6
MAX_SECONDS = 60
SHORT_MEMORY_TURNS = 8


# =========================
# 日志
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("agent_runtime_mvp_v2")


# =========================
# 异常
# =========================

class GuardrailViolation(Exception):
    """
    用于表示运行时违反了系统约束的异常。
    """
    pass


class PermissionDenied(Exception):
    """
    用于表示运行时没有权限执行操作的异常。
    """
    pass


class BudgetExceeded(Exception):
    """
    用于表示运行时预算超出了限制的异常。
    """
    pass


class HumanInterventionRequired(Exception):
    """
    用于表示运行时需要人工干预的异常。
    """
    def __init__(self, request: "HumanInterventionRequest") -> None:
        super().__init__(request.reason)
        self.request = request


# =========================
# 枚举 / 模型
# =========================

class RunStatus(str, enum.Enum):
    """
    运行状态枚举。
    """
    IDLE = "idle"  # 空闲状态
    RUNNING = "running"  # 运行中
    TOOL_RUNNING = "tool_running"  # 工具运行中
    WAITING_HUMAN = "waiting_human"  # 等待人工干预
    COMPLETED = "completed"  # 完成
    DEGRADED = "degraded"  # 降级
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 取消


class Phase(str, enum.Enum):
    """
    运行阶段枚举。
    """
    DECIDING = "deciding"  # 决策中
    TOOL_PENDING = "tool_pending"  # 工具待执行
    TOOL_EXECUTED = "tool_executed"  # 工具执行中
    WAITING_HUMAN = "waiting_human"  # 等待人工干预
    FINALIZING = "finalizing"  # 最终化中
    COMPLETED = "completed"  # 完成


class EventType(str, enum.Enum):
    """
    运行事件类型枚举。
    """
    RUN_STARTED = "run_started"  # 运行开始
    RUN_RESUMED = "run_resumed"  # 运行恢复
    STEP_STARTED = "step_started"  # 步骤开始
    MODEL_OUTPUT = "model_output"  # 模型输出
    TOOL_SELECTED = "tool_selected"  # 工具选择
    TOOL_STARTED = "tool_started"  # 工具开始
    TOOL_FINISHED = "tool_finished"  # 工具完成
    TOOL_FAILED = "tool_failed"  # 工具失败
    HUMAN_REQUIRED = "human_required"  # 人工干预
    HUMAN_APPROVED = "human_approved"  # 人工审批
    HUMAN_REJECTED = "human_rejected"  # 人工拒绝
    STEP_FINISHED = "step_finished"  # 步骤完成
    CHECKPOINT_SAVED = "checkpoint_saved"  # 检查点保存
    RUN_COMPLETED = "run_completed"  # 运行完成
    RUN_DEGRADED = "run_degraded"  # 降级
    RUN_FAILED = "run_failed"  # 运行失败


@dataclass(slots=True)
class SessionMessage:
    """
    会话消息模型。
    """
    role: str  # 角色
    content: str  # 内容
    created_at: float = field(default_factory=time.time)  # 创建时间


@dataclass(slots=True)
class ToolCall:
    """
    工具调用模型。
    """
    call_id: str  # 调用ID
    tool_name: str  # 工具名称
    arguments: dict[str, Any]  # 参数
    idempotency_key: str  # 唯一键值
    metadata: dict[str, Any] = field(default_factory=dict)  # 元数据


@dataclass(slots=True)
class ToolResult:
    """
    工具结果模型。
    """
    call_id: str  # 调用ID
    tool_name: str  # 工具名称
    ok: bool  # 是否成功
    output: str | None = None  # 输出
    error: str | None = None  # 错误
    latency_ms: int = 0  # 延迟时间
    metadata: dict[str, Any] = field(default_factory=dict)  # 元数据


@dataclass(slots=True)
class AgentEvent:
    """
    运行事件模型。
    """
    run_id: str  # 运行ID
    event_type: EventType  # 事件类型
    ts: float  # 时间戳
    step: int  # 步骤
    payload: dict[str, Any] = field(default_factory=dict)  # 事件负载


@dataclass(slots=True)
class HumanInterventionRequest:
    """
    人工干预请求模型。
    """
    reason: str  # 原因
    context: dict[str, Any]  # 上下文
    suggested_actions: list[str] = field(default_factory=list)  # 建议操作


@dataclass(slots=True)
class StepRecord:
    """
    运行步骤记录模型。
    """
    step: int  # 步骤
    phase: str  # 运行阶段
    raw_content: str  # 原始内容
    raw_tool_calls: list[dict[str, Any]]  # 原始工具调用
    model_name: str  # 模型名称


@dataclass(slots=True)
class ToolSpec:
    """
    工具规格模型。
    """
    name: str  # 工具名称
    description: str  # 描述
    parameters: dict[str, Any]  # 参数
    allowed_roles: list[str] = field(default_factory=list)  # 允许角色
    require_approval: bool = False  # 是否需要人工审批
    timeout_s: float = 10.0  # 超时时间（秒）
    readonly: bool = True  # 是否只读模式


@dataclass(slots=True)
class AgentState:
    """
    运行状态模型。
    """
    run_id: str  # 运行ID
    user_id: str  # 用户ID
    task: str  # 任务ID
    session_id: str  # 会话ID
    status: RunStatus = RunStatus.IDLE  # 运行状态
    phase: Phase = Phase.DECIDING  # 运行阶段
    step: int = 0  # 步骤
    started_at: float = field(default_factory=time.time)  # 开始时间
    updated_at: float = field(default_factory=time.time)  # 更新时间
    history: list[StepRecord] = field(default_factory=list)  # 迎�史记录
    tool_results: list[ToolResult] = field(default_factory=list)  # 工具结果
    final_output: str | None = None  # 最终输出
    failure_reason: str | None = None  # 失败原因
    conversation: list[SessionMessage] = field(default_factory=list)  # 会话记录

    # 恢复关键状态
    runtime_messages: list[dict[str, Any]] = field(default_factory=list)  # 运行消息
    pending_tool_call: dict[str, Any] | None = None  # 待处理工具调用
    pending_tool_result: dict[str, Any] | None = None  # 待处理工具结果
    pending_human_request: dict[str, Any] | None = None  # 待处理人工干预

    metadata: dict[str, Any] = field(default_factory=dict)  # 元数据


# =========================
# 工具 / 辅助
# =========================

def ensure_dirs() -> None:
    """
    确保必要的目录存在。
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


def to_jsonable(obj: Any) -> Any:
    """
    将对象转换为可序列化的格式。
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
    安全地将数据转换为 JSON 字符串。
    """
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def serialize_message(msg: Any) -> dict[str, Any]:
    """
    序列化消息为字典。
    """
    if isinstance(msg, SystemMessage):
        return {"type": "system", "content": msg.content}
    if isinstance(msg, HumanMessage):
        return {"type": "human", "content": msg.content}
    if isinstance(msg, ToolMessage):
        return {
            "type": "tool",
            "content": msg.content,
            "tool_call_id": msg.tool_call_id,
        }
    if isinstance(msg, AIMessage):
        return {
            "type": "ai",
            "content": msg.content,
            "tool_calls": getattr(msg, "tool_calls", []) or [],
            "additional_kwargs": getattr(msg, "additional_kwargs", {}) or {},
        }
    raise TypeError(f"不支持的消息类型: {type(msg).__name__}")


def deserialize_message(data: dict[str, Any]) -> Any:
    """
    反序列化字典为消息对象。
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
    raise ValueError(f"未知消息类型: {msg_type}")


def workspace_resolve(path_str: str) -> Path:
    """
    解析工作空间路径。
    """
    base = WORKSPACE_DIR.resolve()
    target = (WORKSPACE_DIR / path_str).resolve()
    if not str(target).startswith(str(base)):
        raise GuardrailViolation(f"路径越界: {path_str}")
    return target


# =========================
# 事件总线
# =========================

class EventSubscriber(Protocol):
    async def handle(self, event: AgentEvent) -> None:
        ...


class EventBus:
    """
    事件总线模型。
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
    日志事件订阅者。
    """
    async def handle(self, event: AgentEvent) -> None:
        logger.info(
            "event=%s run_id=%s step=%s payload=%s",
            event.event_type.value,
            event.run_id,
            event.step,
            event.payload,
        )


# =========================
# 存储
# =========================

class FileSessionStore:
    """
    文件会话存储模型。
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
    文件检查点存储模型。
    """
    def __init__(self, root: Path) -> None:
        """
        初始化检查点存储模型。
        """
        self.root = root

    def _path(self, run_id: str) -> Path:
        """
        获取检查点文件路径。
        """
        return self.root / f"{run_id}.json"

    def save(self, state: AgentState) -> None:
        """
        保存运行状态为检查点。
        """
        payload = to_jsonable(state)
        self._path(state.run_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, run_id: str) -> AgentState | None:
        """
        从检查点加载运行状态。
        """
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


# =========================
# 工具控制链
# =========================

class RetryPolicy:
    """
    重试策略模型。
    """
    def __init__(self, max_retries: int = 2, base_delay: float = 0.4) -> None:
        """
        初始化重试策略模型。
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
        """
        self.max_retries = max_retries
        self.base_delay = base_delay

    async def run(
        self,
        func: Callable[[], Any],
        retryable: Callable[[Exception], bool] | None = None,
    ) -> Any:
        """
        执行函数并处理重试策略。
        func: 要执行的异步函数:也就是工具调用函数
        retryable: 可选的重试判断函数
        """
        last_error: Optional[Exception] = None
        retryable = retryable or (lambda exc: True)

        for attempt in range(self.max_retries + 1):
            try:
                return await func()
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries or not retryable(exc):
                    break
                await asyncio.sleep(self.base_delay * (2**attempt))

        assert last_error is not None
        raise last_error


class IdempotencyStore:
    """
    唯一键值存储模型。
    用于缓存工具调用结果，避免重复执行相同调用。
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
    权限控制器模型。
    """
    def __init__(self, role_getter: Callable[[str], str]) -> None:
        self._role_getter = role_getter

    def check_tool_permission(self, state: AgentState, spec: ToolSpec) -> None:
        role = self._role_getter(state.user_id)
        if spec.allowed_roles and role not in spec.allowed_roles:
            raise PermissionDenied(f"角色 '{role}' 无法访问工具 '{spec.name}'")


class ApprovalController:
    """
    人工审批控制器模型。
    """
    async def require_approval_if_needed(self, spec: ToolSpec, call: ToolCall) -> None:
        approved = bool(call.metadata.get("approved", False))
        if spec.require_approval and not approved:
            raise HumanInterventionRequired(
                HumanInterventionRequest(
                    reason=f"工具 '{spec.name}' 需要人工审批",
                    context={"tool_call": to_jsonable(call)},
                    suggested_actions=["approve", "reject", "edit_arguments"],
                )
            )


class GuardrailEngine:
    """
    守卫引擎模型。 
    用于验证用户输入和工具调用参数，防止恶意操作。
    """
    def validate_user_input(self, task: str) -> None:
        blocked = ["steal secrets", "delete production database"]
        lower_task = task.lower()
        for pattern in blocked:
            if pattern in lower_task:
                raise GuardrailViolation(f"检测到被阻止的任务模式: {pattern}")

    def validate_tool_args(self, tool_name: str, arguments: dict[str, object]) -> None:
        if not isinstance(arguments, dict):
            raise GuardrailViolation(f"工具 '{tool_name}' 的参数必须是字典类型")

        if tool_name in {"read_file", "write_note"}:
            path = arguments.get("path")
            if not isinstance(path, str) or not path.strip():
                raise GuardrailViolation(f"工具 '{tool_name}' 缺少合法 path 参数")
            workspace_resolve(path)

    def validate_final_output(self, output: str) -> None:
        if not output.strip():
            raise GuardrailViolation("最终输出不能为空")


# =========================
# 工具协议
# =========================

class BaseTool(Protocol):
    """
    基础工具协议。
    """
    async def arun(self, arguments: dict[str, Any]) -> str:
        ...


class ToolRegistry:
    """
    工具注册器模型。
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
            raise ValueError(f"未知工具 spec: {name}")
        return spec

    def get_tool(self, name: str) -> BaseTool:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"未知工具实现: {name}")
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


# =========================
# 具体工具
# =========================

class GetCurrentTimeTool:
    """
    获取当前时间工具。
    """
    async def arun(self, arguments: dict[str, Any]) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class CalculatorTool:
    """
    计算工具。
    """
    async def arun(self, arguments: dict[str, Any]) -> str:
        expression = arguments.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("calculator 需要 expression 字符串参数")

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
                raise ValueError(f"不允许的表达式节点: {type(node).__name__}")
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                raise ValueError("只允许数字常量")

        value = eval(compile(tree, filename="<expr>", mode="eval"), {"__builtins__": {}}, {})
        return str(value)


class ReadFileTool:
    """
    读取文件工具。
    """
    async def arun(self, arguments: dict[str, Any]) -> str:
        path = workspace_resolve(arguments["path"])
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path.name}")
        if path.is_dir():
            raise IsADirectoryError(f"不能读取目录: {path.name}")
        return path.read_text(encoding="utf-8")


class WriteNoteTool:
    """
    写入笔记工具。
    """
    async def arun(self, arguments: dict[str, Any]) -> str:
        path = workspace_resolve(arguments["path"])
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("write_note 需要 content 字符串参数")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"已写入文件: {path.relative_to(WORKSPACE_DIR)}"


# =========================
# 工具执行器
# =========================

class ToolExecutor:
    """
    工具执行器模型。
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
        """
        执行工具调用。
        """
        # 1 获取工具 spec
        spec = self.registry.get_spec(call.tool_name)
        # 2 获取工具实现
        tool = self.registry.get_tool(call.tool_name)
        # 3 检查工具权限
        self.permission_controller.check_tool_permission(state, spec)
        # 4.需不需要人工审批
        await self.approval_controller.require_approval_if_needed(spec, call)
        # 5. 参数护栏检查 → 工具参数是否合法、安全？
        self.guardrail_engine.validate_tool_args(call.tool_name, call.arguments)
        #  如果是幂等调用，先检查缓存
        if call.idempotency_key:
            cached = self.idempotency_store.get(call.idempotency_key)
            if cached is not None:
                return cached
        # 检查过后，发布工具开始事件
        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.TOOL_STARTED,
                ts=time.time(),
                step=state.step,
                payload={"tool_name": call.tool_name, "arguments": call.arguments},
            )
        )
        # 定义工具调用函数
        async def _invoke() -> str:
            return await asyncio.wait_for(tool.arun(call.arguments), timeout=spec.timeout_s)
        # 定义 重试判断函数
        def _retryable(exc: Exception) -> bool:
            """
            异常捕获，判断是否需要重试。
            重试：GuardrailViolation, PermissionDenied, HumanInterventionRequired, ValueError
            """
            return not isinstance(
                exc,
                (GuardrailViolation, PermissionDenied, HumanInterventionRequired, ValueError),
            )
        
        start = time.time()
        try:
            output = await self.retry_policy.run(_invoke, retryable=_retryable)
            result = ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=True,
                output=output,
                latency_ms=int((time.time() - start) * 1000),
            )
        #8. 处理异常
        except Exception as exc:
            result = ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=False,
                error=str(exc),
                latency_ms=int((time.time() - start) * 1000),
                metadata={"error_type": type(exc).__name__},
            )
        #9. 缓存结果
        if call.idempotency_key:
            self.idempotency_store.set(call.idempotency_key, result)
        #10. 发布工具完成事件
        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.TOOL_FINISHED if result.ok else EventType.TOOL_FAILED,
                ts=time.time(),
                step=state.step,
                payload={
                    "tool_name": call.tool_name,
                    "ok": result.ok,
                    "error": result.error,
                },
            )
        )
        return result


# =========================
# LLM 客户端
# =========================

class NativeToolCallingLLMClient:
    def __init__(self, llm: Any, model_name: str, tool_schemas: list[dict[str, Any]]) -> None:
        self.model_name = model_name
        self.raw_llm = llm
        self.bound_llm = self._bind_tools(llm, tool_schemas)

    def _bind_tools(self, llm: Any, tool_schemas: list[dict[str, Any]]) -> Any:
        if hasattr(llm, "bind_tools"):
            return llm.bind_tools(tool_schemas)
        raise RuntimeError("当前 llm 不支持 bind_tools")

    async def invoke(self, messages: list[Any]) -> Any:
        if hasattr(self.bound_llm, "ainvoke"):
            return await self.bound_llm.ainvoke(messages)
        return await asyncio.to_thread(self.bound_llm.invoke, messages)
    # 从模型响应中提取内容和工具调用
    def extract_content_and_tool_calls(self, response: Any) -> tuple[str, list[dict[str, Any]]]:
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
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts).strip()
        return str(content)


# =========================
# 消息构造
# =========================

# TODO 提示词方面有些死板，关于工具的提示词需要动态加载而不是直接在SystemMessage中固定写它有什么什么工具调用
class MessageBuilder:
    def __init__(self, short_memory_turns: int = SHORT_MEMORY_TURNS) -> None:
        self.short_memory_turns = short_memory_turns

    def build_initial_messages(self, state: AgentState) -> list[Any]:
        recent = state.conversation[-self.short_memory_turns :]
        messages: list[Any] = [
            SystemMessage(
                content=(
                    "你是一个中文智能助手。\n"
                    "优先直接回答；只有在确实需要外部能力时才调用工具。\n"
                    "不要伪造工具结果。\n"
                    "数学计算、时间查询、读取文件可以调用工具。\n"
                    "需要写文件时，可以使用 write_note 工具。"
                )
            )
        ]

        for msg in recent:
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                messages.append(AIMessage(content=msg.content))

        return messages


# =========================
# Runtime
# =========================

class AgentRuntime:
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
            raise ValueError(f"未找到 checkpoint: {run_id}")
        snapshot = state.metadata.get("tool_ledger", {})
        if isinstance(snapshot, dict):
            self.idempotency_store.load_snapshot(snapshot)
        return state

    async def resume(
        self,
        run_id: str,
        human_decision: dict[str, Any] | None = None,
    ) -> AgentState:
        state = await self.restore(run_id)

        if state.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return state

        if state.phase == Phase.WAITING_HUMAN:
            if human_decision is None:
                raise ValueError("当前 run 正在等待人工审批，需要提供 human_decision")

            approved = bool(human_decision.get("approved", False))
            edited_arguments = human_decision.get("edited_arguments")

            if not approved:
                state.status = RunStatus.COMPLETED
                state.phase = Phase.COMPLETED
                state.final_output = "人工审批已拒绝，本次执行终止。"
                state.pending_human_request = None
                await self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.HUMAN_REJECTED,
                        ts=time.time(),
                        step=state.step,
                        payload={"reason": "审批拒绝"},
                    )
                )
                await self._finalize(state)
                return state

            if state.pending_tool_call is None:
                raise ValueError("等待审批状态下缺少 pending_tool_call")

            if edited_arguments is not None:
                if not isinstance(edited_arguments, dict):
                    raise ValueError("edited_arguments 必须是 dict")
                state.pending_tool_call["arguments"] = edited_arguments

            state.pending_tool_call.setdefault("metadata", {})
            state.pending_tool_call["metadata"]["approved"] = True
            state.pending_human_request = None
            state.status = RunStatus.RUNNING
            state.phase = Phase.TOOL_PENDING

            await self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.HUMAN_APPROVED,
                    ts=time.time(),
                    step=state.step,
                    payload={"tool_name": state.pending_tool_call["tool_name"]},
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
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.STEP_STARTED,
                        ts=time.time(),
                        step=state.step,
                    )
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
                    answer = (content or "").strip() or "本次运行没有生成有效回答。"
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
                    idempotency_key=(
                        f"{state.run_id}:{raw_call['name']}:"
                        f"{safe_json_dumps(raw_call['arguments'])}"
                    ),
                )
                state.pending_tool_call = to_jsonable(call)
                state.phase = Phase.TOOL_PENDING

                await self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.TOOL_SELECTED,
                        ts=time.time(),
                        step=state.step,
                        payload={
                            "tool_name": call.tool_name,
                            "arguments": call.arguments,
                        },
                    )
                )
                await self._save_checkpoint(state)
                continue

            if state.phase == Phase.TOOL_PENDING:
                if state.pending_tool_call is None:
                    raise RuntimeError("TOOL_PENDING 但没有 pending_tool_call")

                call = ToolCall(**state.pending_tool_call)
                state.status = RunStatus.TOOL_RUNNING

                try:
                    # result = await self.tool_executor.execute(state, call)
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
                            payload={
                                "reason": exc.request.reason,
                                "tool_name": call.tool_name,
                            },
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
                # await self._save_checkpoint(state)
                # continue

            if state.phase == Phase.TOOL_EXECUTED:
                if state.pending_tool_call is None or state.pending_tool_result is None:
                    raise RuntimeError("TOOL_EXECUTED 缺少 pending_tool_call 或 pending_tool_result")

                call = ToolCall(**state.pending_tool_call)
                result = ToolResult(**state.pending_tool_result)

                runtime_messages.append(
                    ToolMessage(
                        content=result.output if result.ok else (result.error or ""),
                        tool_call_id=call.call_id,
                    )
                )
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

            if state.phase == Phase.WAITING_HUMAN:
                return state

            if state.phase == Phase.COMPLETED:
                return state

            raise RuntimeError(f"未知 phase: {state.phase}")

    async def _save_checkpoint(self, state: AgentState) -> None:
        self.checkpoint_store.save(state)
        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.CHECKPOINT_SAVED,
                ts=time.time(),
                step=state.step,
            )
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
    def __init__(self, max_steps: int, max_tool_calls: int, max_seconds: int) -> None:
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.max_seconds = max_seconds

    def check(self, state: AgentState) -> None:
        if state.step >= self.max_steps:
            raise BudgetExceeded(f"超过最大步骤数: {self.max_steps}")
        if len(state.tool_results) >= self.max_tool_calls:
            raise BudgetExceeded(f"超过最大工具调用次数: {self.max_tool_calls}")
        if time.time() - state.started_at >= self.max_seconds:
            raise BudgetExceeded(f"超过最大运行时间: {self.max_seconds}s")


# =========================
# 组装
# =========================

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
    registry.register(
        ToolSpec(
            name="get_current_time",
            description="获取当前本地时间",
            parameters={"type": "object", "properties": {}, "required": []},
            readonly=True,
        ),
        GetCurrentTimeTool(),
    )
    registry.register(
        ToolSpec(
            name="calculator",
            description="做基础数学运算，输入 expression，例如 88+666 或 ((12+8)*3)/5",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "要计算的数学表达式",
                    }
                },
                "required": ["expression"],
            },
            readonly=True,
        ),
        CalculatorTool(),
    )
    registry.register(
        ToolSpec(
            name="read_file",
            description="读取 workspace 内某个文件的内容，例如 notes/todo.txt",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 workspace 的文件路径"}
                },
                "required": ["path"],
            },
            readonly=True,
        ),
        ReadFileTool(),
    )
    registry.register(
        ToolSpec(
            name="write_note",
            description="向 workspace 内写入一个文本文件，需要人工审批。适合记录笔记或生成文档。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 workspace 的文件路径"},
                    "content": {"type": "string", "description": "要写入的内容"},
                },
                "required": ["path", "content"],
            },
            require_approval=True,
            readonly=False,
        ),
        WriteNoteTool(),
    )

    event_bus = EventBus()
    event_bus.subscribe(LoggingSubscriber())

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


# =========================
# CLI
# =========================

HELP = """
命令：
1. 普通聊天 / 任务
   直接输入一句话，例如：
   - 88+666是多少
   - 现在几点
   - 读取 notes/demo.txt
   - 请帮我把今天的想法写到 notes/today.txt

2. 恢复运行
   /resume <run_id>

3. 审批通过并继续
   /approve <run_id>

4. 审批拒绝
   /reject <run_id>

5. 退出
   exit
""".strip()


async def main() -> None:
    runtime = build_runtime()
    user_id = "demo_user"
    session_id = "demo_session"

    print("restore + resume 工业 loop MVP 已启动")
    print(HELP)
    print(f"workspace 目录: {WORKSPACE_DIR.resolve()}")

    last_run_id: str | None = None

    while True:
        user_input = input("\nUser> ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue

        try:
            if user_input.startswith("/resume "):
                run_id = user_input.split(" ", 1)[1].strip()
                state = await runtime.resume(run_id)
            elif user_input.startswith("/approve "):
                run_id = user_input.split(" ", 1)[1].strip()
                state = await runtime.resume(run_id, human_decision={"approved": True})
            elif user_input.startswith("/reject "):
                run_id = user_input.split(" ", 1)[1].strip()
                state = await runtime.resume(run_id, human_decision={"approved": False})
            else:
                state = await runtime.chat(
                    user_id=user_id,
                    session_id=session_id,
                    message=user_input,
                )

            last_run_id = state.run_id
            print(f"\nrun_id: {state.run_id}")
            print(f"status: {state.status.value}")
            print(f"phase: {state.phase.value}")
            print(f"answer: {state.final_output or state.failure_reason}")

            if state.status == RunStatus.WAITING_HUMAN:
                print("当前运行正在等待人工审批。可使用：")
                print(f"/approve {state.run_id}")
                print(f"/reject {state.run_id}")

        except Exception as exc:
            print(f"发生错误: {type(exc).__name__}: {exc}")
            if last_run_id:
                print(f"最近 run_id: {last_run_id}")


if __name__ == "__main__":
    asyncio.run(main())