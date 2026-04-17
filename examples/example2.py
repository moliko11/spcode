from __future__ import annotations

import dataclasses
import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.model_loader import create_model_loader

# 这几个类默认按 LangChain 风格来
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


# =========================
# 配置
# =========================

MODEL_URL = "http://10.8.160.47:9998/v1"
MODEL_NAME = "qwen3"
API_KEY = "EMPTY"
TEMPERATURE = 0.5

SESSION_DIR = Path("./runtime_data/sessions")
CHECKPOINT_DIR = Path("./runtime_data/checkpoints")

MAX_STEPS = 8
MAX_TOOL_CALLS = 4
MAX_SECONDS = 45
SHORT_MEMORY_TURNS = 8


# =========================
# 日志
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("native_toolcall_agent_mvp")


# =========================
# 数据结构
# =========================

class RunStatus(str, enum.Enum):
    """
    运行状态
    """
    IDLE = "idle"  # 空闲状态
    RUNNING = "running"  # 运行中
    TOOL_RUNNING = "tool_running"  # 工具运行中
    WAITING_HUMAN = "waiting_human"  # 等待人类输入
    COMPLETED = "completed"  # 完成
    DEGRADED = "degraded"  # 降级
    FAILED = "failed"  # 失败


class EventType(str, enum.Enum):
    """
    事件类型
    """
    RUN_STARTED = "run_started"  # 运行开始
    STEP_STARTED = "step_started"  # 步骤开始
    MODEL_OUTPUT = "model_output"  # 模型输出
    TOOL_SELECTED = "tool_selected"  # 工具选择
    TOOL_STARTED = "tool_started"  # 工具开始
    TOOL_FINISHED = "tool_finished"  # 工具完成
    TOOL_FAILED = "tool_failed"  # 工具失败
    HUMAN_REQUIRED = "human_required"  # 需要人类输入
    STEP_FINISHED = "step_finished"  # 步骤完成
    CHECKPOINT_SAVED = "checkpoint_saved"  # 检查点保存
    RUN_COMPLETED = "run_completed"  # 运行完成
    RUN_DEGRADED = "run_degraded"  # 运行降级
    RUN_FAILED = "run_failed"  # 运行失败


@dataclass(slots=True)
class SessionMessage:
    role: str  # 角色
    content: str  # 内容
    created_at: float = field(default_factory=time.time)  # 创建时间


@dataclass(slots=True)
class ToolCall:
    """
    工具调用
    """
    call_id: str  # 调用ID
    tool_name: str  # 工具名称
    arguments: dict[str, Any]  # 参数
    idempotency_key: str  # 唯一键值


@dataclass(slots=True)
class ToolResult:
    call_id: str
    tool_name: str
    output: str
    success: bool
    latency_ms: int
    error: str | None = None


@dataclass(slots=True)
class AgentEvent:
    """
    代理事件
    """
    run_id: str  # 运行ID
    event_type: EventType  # 事件类型
    ts: float  # 时间戳
    step: int  # 步骤
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StepRecord:
    """
    步骤记录
    """
    step: int  # 步骤
    tool_name: str | None  # 工具名称
    tool_arguments: dict[str, Any]  # 工具参数
    raw_content: str
    raw_tool_calls: list[dict[str, Any]]  # 原始工具调用
    model_name: str  # 模型名称


@dataclass(slots=True)
class AgentState:
    """
    代理状态
    """
    run_id: str  # 运行ID
    user_id: str  # 用户ID
    task: str  # 任务ID
    session_id: str  # 会话ID
    status: RunStatus = RunStatus.IDLE
    step: int = 0  # 步骤
    started_at: float = field(default_factory=time.time)  # 开始时间
    updated_at: float = field(default_factory=time.time)  # 更新时间
    history: list[StepRecord] = field(default_factory=list)  # 步骤记录
    tool_results: list[ToolResult] = field(default_factory=list)  # 工具结果
    final_output: str | None = None  # 最终输出
    failure_reason: str | None = None  # 失败原因
    conversation: list[SessionMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)  # 元数据


# =========================
# 基础设施
# =========================

def ensure_dirs() -> None:
    """
    确保会话目录和检查点目录存在。
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


def to_jsonable(obj: Any) -> Any:
    """
    将对象转换为可序列化的 JSON 格式。
    :param obj: 要转换的对象
    :return: 可序列化的 JSON 格式
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


class EventBus:
    """
    事件总线
    """
    def __init__(self) -> None:
        self._subscribers: list[Any] = []

    def subscribe(self, subscriber: Any) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def publish(self, event: AgentEvent) -> None:
        subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.handle(event)
            except Exception:
                logger.exception("event subscriber failed: %s", subscriber.__class__.__name__)


class LoggingSubscriber:
    """
    日志订阅者
    """
    def handle(self, event: AgentEvent) -> None:
        logger.info(
            "event=%s run_id=%s step=%s payload=%s",
            event.event_type.value,
            event.run_id,
            event.step,
            event.payload,
        )


class FileSessionStore:
    """
    文件会话存储
    """
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

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
    文件检查点存储
    """
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def save(self, state: AgentState) -> None:
        payload = {
            "run_id": state.run_id,
            "user_id": state.user_id,
            "task": state.task,
            "session_id": state.session_id,
            "status": state.status.value,
            "step": state.step,
            "started_at": state.started_at,
            "updated_at": state.updated_at,
            "history": [to_jsonable(h) for h in state.history],
            "tool_results": [to_jsonable(r) for r in state.tool_results],
            "final_output": state.final_output,
            "failure_reason": state.failure_reason,
            "conversation": [to_jsonable(m) for m in state.conversation],
            "metadata": state.metadata,
        }
        self._path(state.run_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# =========================
# 预算与循环
# =========================

class BudgetExceeded(Exception):
    """
    预算超出异常
    """
    pass


class HumanApprovalRequired(Exception):
    """
    需要人类审批异常
    """
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BudgetController:
    """
    预算控制器
    """
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

class SemanticLoopDetector:
    """
    语义循环检测器
    """
    def is_looping(self, state: AgentState, tool_calls: list[dict[str, Any]], content: str) -> bool:
        signature = {
            "tool_calls": tool_calls,
            "content": (content or "").strip()[:200],
        }
        signatures = state.metadata.get("loop_signatures", [])
        signatures.append(signature)
        signatures = signatures[-4:]
        state.metadata["loop_signatures"] = signatures

        if len(signatures) >= 3:
            last3 = signatures[-3:]
            if last3[0] == last3[1] == last3[2]:
                return True
        return False


# =========================
# 工具定义
# =========================

@dataclass(slots=True)
class ToolSpec:
    """
    工具规范
    """
    name: str
    description: str
    parameters: dict[str, Any]
    requires_human_approval: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._impls: dict[str, Any] = {}

    def register(self, spec: ToolSpec, impl: Any) -> None:
        self._specs[spec.name] = spec
        self._impls[spec.name] = impl

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def get_impl(self, name: str) -> Any | None:
        return self._impls.get(name)

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


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, event_bus: EventBus) -> None:
        self.registry = registry
        self.event_bus = event_bus

    def execute(self, state: AgentState, call: ToolCall) -> ToolResult:
        spec = self.registry.get_spec(call.tool_name)
        impl = self.registry.get_impl(call.tool_name)

        if spec is None or impl is None:
            raise ValueError(f"未知工具: {call.tool_name}")

        if spec.requires_human_approval:
            raise HumanApprovalRequired(f"工具 {call.tool_name} 需要人工审批后才能执行")

        self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.TOOL_STARTED,
                ts=time.time(),
                step=state.step,
                payload={"tool_name": call.tool_name, "arguments": call.arguments},
            )
        )

        start = time.time()
        state.status = RunStatus.TOOL_RUNNING

        try:
            output = impl(**call.arguments)
            result = ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                output=str(output),
                success=True,
                latency_ms=int((time.time() - start) * 1000),
            )
            self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.TOOL_FINISHED,
                    ts=time.time(),
                    step=state.step,
                    payload={"tool_name": call.tool_name, "output": result.output},
                )
            )
            return result
        except Exception as exc:
            result = ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                output="",
                success=False,
                latency_ms=int((time.time() - start) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
            self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.TOOL_FAILED,
                    ts=time.time(),
                    step=state.step,
                    payload={"tool_name": call.tool_name, "error": result.error},
                )
            )
            return result
        finally:
            state.status = RunStatus.RUNNING


# =========================
# 示例工具
# =========================

def get_current_time() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def calculator(expression: str) -> str:
    import ast

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


# =========================
# LLM 适配层
# =========================

class NativeToolCallingLLMClient:
    """
    默认按 LangChain 风格模型封装：
    - llm.bind_tools(openai_tool_schemas)
    - llm.invoke(messages)
    """

    def __init__(self, llm: Any, model_name: str, tool_schemas: list[dict[str, Any]]) -> None:
        self.model_name = model_name
        self.raw_llm = llm
        self.bound_llm = self._bind_tools(llm, tool_schemas)

    def _bind_tools(self, llm: Any, tool_schemas: list[dict[str, Any]]) -> Any:
        if hasattr(llm, "bind_tools"):
            return llm.bind_tools(tool_schemas)
        raise RuntimeError(
            "当前 llm 对象不支持 bind_tools。"
            "如果你的 loader 不是 LangChain 风格，请在 loader 层暴露原始 OpenAI client。"
        )

    def invoke(self, messages: list[Any]) -> Any:
        return self.bound_llm.invoke(messages)

    def extract_content_and_tool_calls(self, response: Any) -> tuple[str, list[dict[str, Any]]]:
        """
        从模型响应中提取内容和工具调用。
        :param response: 模型响应对象
        :return: 包含内容和工具调用的元组
        """
        content = getattr(response, "content", "") or ""

        # LangChain AIMessage 常见路径
        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            normalized = []
            for item in tool_calls:
                # 常见格式：{"name": ..., "args": ..., "id": ..., "type": "tool_call"}
                normalized.append(
                    {
                        "id": item.get("id") or str(uuid.uuid4()),
                        "name": item.get("name"),
                        "arguments": item.get("args", {}) or {},
                    }
                )
            return content, normalized

        # 兼容 additional_kwargs
        additional_kwargs = getattr(response, "additional_kwargs", {}) or {}
        raw_tool_calls = additional_kwargs.get("tool_calls", []) or []
        if raw_tool_calls:
            normalized = []
            for item in raw_tool_calls:
                fn = item.get("function", {}) or {}
                arguments = fn.get("arguments", "{}")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                normalized.append(
                    {
                        "id": item.get("id") or str(uuid.uuid4()),
                        "name": fn.get("name"),
                        "arguments": arguments if isinstance(arguments, dict) else {},
                    }
                )
            return content, normalized

        return content, []


# =========================
# Prompt / Messages
# =========================

class MessageBuilder:
    def __init__(self, short_memory_turns: int = SHORT_MEMORY_TURNS) -> None:
        self.short_memory_turns = short_memory_turns

    def build_messages(self, state: AgentState) -> list[Any]:
        recent_messages = state.conversation[-self.short_memory_turns :]

        system_prompt = (
            "你是一个中文智能助手。\n"
            "优先直接回答；只有在确实需要外部能力时才调用工具。\n"
            "不要伪造工具结果。\n"
            "已知工具结果足够时，请直接给最终答案。\n"
            "如果用户问时间、计算等问题，可以使用相应工具。\n"
            "回答简洁、准确。"
        )

        messages: list[Any] = [SystemMessage(content=system_prompt)]

        for msg in recent_messages:
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                messages.append(AIMessage(content=msg.content))
            elif msg.role == "tool":
                # SessionStore 里暂不持久化 tool call id，所以这里只回填普通内容到对话记忆
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
        budget_controller: BudgetController,
        loop_detector: SemanticLoopDetector,
    ) -> None:
        self.llm_client = llm_client
        self.message_builder = message_builder
        self.tool_executor = tool_executor
        self.registry = registry
        self.session_store = session_store
        self.checkpoint_store = checkpoint_store
        self.event_bus = event_bus
        self.budget_controller = budget_controller
        self.loop_detector = loop_detector

    def chat(self, user_id: str, session_id: str, message: str) -> AgentState:
        if not message.strip():
            raise ValueError("用户输入不能为空")

        previous = self.session_store.load_messages(session_id)
        self.session_store.append_message(session_id, "user", message)

        state = AgentState(
            run_id=str(uuid.uuid4()),
            user_id=user_id,
            task=message,
            session_id=session_id,
            status=RunStatus.RUNNING,
            conversation=previous + [SessionMessage(role="user", content=message)],
        )

        self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.RUN_STARTED,
                ts=time.time(),
                step=state.step,
                payload={"task": message, "session_id": session_id},
            )
        )

        try:
            while True:
                state.updated_at = time.time()
                self.budget_controller.check(state)

                self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.STEP_STARTED,
                        ts=time.time(),
                        step=state.step,
                    )
                )

                messages = self.message_builder.build_messages(state)
                response = self.llm_client.invoke(messages)
                content, tool_calls = self.llm_client.extract_content_and_tool_calls(response)

                self.event_bus.publish(
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
                        tool_name=tool_calls[0]["name"] if tool_calls else None,
                        tool_arguments=tool_calls[0]["arguments"] if tool_calls else {},
                        raw_content=content,
                        raw_tool_calls=tool_calls,
                        model_name=self.llm_client.model_name,
                    )
                )

                if self.loop_detector.is_looping(state, tool_calls, content):
                    state.status = RunStatus.DEGRADED
                    state.failure_reason = "检测到重复循环"
                    state.final_output = self._build_recovery_answer(state)
                    self._save_checkpoint(state)
                    self.event_bus.publish(
                        AgentEvent(
                            run_id=state.run_id,
                            event_type=EventType.RUN_DEGRADED,
                            ts=time.time(),
                            step=state.step,
                            payload={"reason": state.failure_reason},
                        )
                    )
                    if state.final_output:
                        self.session_store.append_message(session_id, "assistant", state.final_output)
                    return state

                # 没有 tool_calls，直接认为是最终回答
                if not tool_calls:
                    answer = (content or "").strip()
                    if not answer:
                        answer = "本次运行没有生成有效回答。"
                    state.final_output = answer
                    state.status = RunStatus.COMPLETED
                    self._save_checkpoint(state)
                    self.event_bus.publish(
                        AgentEvent(
                            run_id=state.run_id,
                            event_type=EventType.RUN_COMPLETED,
                            ts=time.time(),
                            step=state.step,
                            payload={"final_output": state.final_output},
                        )
                    )
                    self.session_store.append_message(session_id, "assistant", state.final_output)
                    return state

                # 当前 MVP 先只执行一个 tool call；后面可扩成并发多工具
                raw_call = tool_calls[0]
                tool_name = raw_call["name"]
                arguments = raw_call["arguments"]

                self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.TOOL_SELECTED,
                        ts=time.time(),
                        step=state.step,
                        payload={"tool_name": tool_name, "arguments": arguments},
                    )
                )

                call = ToolCall(
                    call_id=raw_call["id"],
                    tool_name=tool_name,
                    arguments=arguments,
                    idempotency_key=(
                        f"{state.run_id}:{tool_name}:"
                        f"{json.dumps(arguments, ensure_ascii=False, sort_keys=True)}"
                    ),
                )

                try:
                    result = self.tool_executor.execute(state, call)
                except HumanApprovalRequired as exc:
                    state.status = RunStatus.WAITING_HUMAN
                    state.failure_reason = exc.reason
                    self._save_checkpoint(state)
                    self.event_bus.publish(
                        AgentEvent(
                            run_id=state.run_id,
                            event_type=EventType.HUMAN_REQUIRED,
                            ts=time.time(),
                            step=state.step,
                            payload={"reason": exc.reason, "tool_name": tool_name},
                        )
                    )
                    return state

                state.tool_results.append(result)

                # 把工具结果写回短时记忆
                state.conversation.append(
                    SessionMessage(
                        role="assistant",
                        content=f"[工具调用] {tool_name}({json.dumps(arguments, ensure_ascii=False)})",
                    )
                )
                state.conversation.append(
                    SessionMessage(
                        role="tool",
                        content=f"[工具结果] {result.output if result.success else result.error}",
                    )
                )

                self._save_checkpoint(state)
                self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.STEP_FINISHED,
                        ts=time.time(),
                        step=state.step,
                        payload={"tool_name": tool_name, "success": result.success},
                    )
                )
                state.step += 1

        except BudgetExceeded as exc:
            state.status = RunStatus.DEGRADED
            state.failure_reason = str(exc)
            state.final_output = self._build_recovery_answer(state)
            self._save_checkpoint(state)
            self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.RUN_DEGRADED,
                    ts=time.time(),
                    step=state.step,
                    payload={"reason": state.failure_reason},
                )
            )
            if state.final_output:
                self.session_store.append_message(session_id, "assistant", state.final_output)
            return state

        except Exception as exc:
            state.status = RunStatus.FAILED
            state.failure_reason = f"{type(exc).__name__}: {exc}"
            self._save_checkpoint(state)
            self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.RUN_FAILED,
                    ts=time.time(),
                    step=state.step,
                    payload={"error": state.failure_reason},
                )
            )
            return state

    def _save_checkpoint(self, state: AgentState) -> None:
        self.checkpoint_store.save(state)
        self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.CHECKPOINT_SAVED,
                ts=time.time(),
                step=state.step,
            )
        )

    def _build_recovery_answer(self, state: AgentState) -> str:
        if state.tool_results:
            last = state.tool_results[-1]
            if last.success:
                return f"本次运行提前收敛，基于最近一次工具结果给出答复：\n{last.output}"
            return f"本次运行提前收敛，但最近一次工具调用失败：{last.error}"
        return "本次运行提前结束，暂时没有得到足够稳定的结果。"


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
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            requires_human_approval=False,
        ),
        get_current_time,
    )
    registry.register(
        ToolSpec(
            name="calculator",
            description="做基础数学运算，输入 expression，例如 ((12+8)*3)/5",
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
            requires_human_approval=False,
        ),
        calculator,
    )

    event_bus = EventBus()
    event_bus.subscribe(LoggingSubscriber())

    llm_client = NativeToolCallingLLMClient(
        llm=llm,
        model_name=MODEL_NAME,
        tool_schemas=registry.openai_tools(),
    )

    return AgentRuntime(
        llm_client=llm_client,
        message_builder=MessageBuilder(short_memory_turns=SHORT_MEMORY_TURNS),
        tool_executor=ToolExecutor(registry=registry, event_bus=event_bus),
        registry=registry,
        session_store=FileSessionStore(SESSION_DIR),
        checkpoint_store=FileCheckpointStore(CHECKPOINT_DIR),
        event_bus=event_bus,
        budget_controller=BudgetController(
            max_steps=MAX_STEPS,
            max_tool_calls=MAX_TOOL_CALLS,
            max_seconds=MAX_SECONDS,
        ),
        loop_detector=SemanticLoopDetector(),
    )


# =========================
# CLI
# =========================

def main() -> None:
    runtime = build_runtime()
    user_id = "demo_user"
    session_id = "demo_session"

    print("Native tool calling 工业 loop MVP 已启动")
    print("输入 exit 退出")
    print("你可以试试：")
    print("- 我叫小王")
    print("- 你还记得我叫什么吗")
    print("- 现在几点")
    print("- 帮我算一下 ((12+8)*3)/5")
    print("- 根据我们刚才的对话总结一下")
    print("-" * 60)

    while True:
        user_input = input("\nUser> ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue

        state = runtime.chat(
            user_id=user_id,
            session_id=session_id,
            message=user_input,
        )
        print(f"\nAssistant[{state.status.value}]> {state.final_output or state.failure_reason}")


if __name__ == "__main__":
    main()