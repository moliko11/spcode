from __future__ import annotations

import ast
import dataclasses
import enum
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from packages.model_loader import create_model_loader


# =========================
# 配置区
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
SHORT_MEMORY_TURNS = 8  # 短时记忆窗口：最近 N 条消息


# =========================
# 日志
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("agent_loop_mvp")


# =========================
# 数据模型
# =========================

class RunStatus(str, enum.Enum):
    IDLE = "idle"  # 空闲
    RUNNING = "running"  # 运行中
    TOOL_RUNNING = "tool_running"  # 工具运行中
    WAITING_HUMAN = "waiting_human"  # 等待人工介入
    COMPLETED = "completed"  # 完成
    DEGRADED = "degraded"  # 降级
    FAILED = "failed"  # 失败


class EventType(str, enum.Enum):
    RUN_STARTED = "run_started"  # 运行开始
    STEP_STARTED = "step_started"  # 步骤开始
    MODEL_OUTPUT = "model_output"  # 模型输出
    TOOL_SELECTED = "tool_selected"  # 工具选择
    TOOL_STARTED = "tool_started"  # 工具开始
    TOOL_FINISHED = "tool_finished"  # 工具完成
    TOOL_FAILED = "tool_failed"  # 工具失败
    STEP_FINISHED = "step_finished"  # 步骤完成
    CHECKPOINT_SAVED = "checkpoint_saved"  # 检查点保存
    HUMAN_REQUIRED = "human_required"  # 人工介入
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
    call_id: str  # 调用 ID
    tool_name: str  # 工具名称
    arguments: dict[str, Any]  # 参数
    idempotency_key: str  # 唯一键值


@dataclass(slots=True)
class ToolResult:
    call_id: str  # 调用 ID
    tool_name: str  # 工具名称
    output: str  # 输出
    success: bool  # 是否成功
    latency_ms: int  # 响应时间，单位毫秒
    error: str | None = None  # 错误信息，如果失败


@dataclass(slots=True)
class AgentEvent:
    run_id: str  # 运行 ID
    event_type: EventType  # 事件类型
    ts: float  # 时间戳
    step: int  # 步骤数
    payload: dict[str, Any] = field(default_factory=dict)  # 事件有效负载


@dataclass(slots=True)
class ModelDecision:
    action: str  # 动作
    thought: str = ""  # 思考
    tool_name: str | None = None  # 工具名称
    tool_arguments: dict[str, Any] = field(default_factory=dict)  # 工具参数
    answer: str | None = None  # 回答
    reason: str | None = None  # 原因


@dataclass(slots=True)
class StepRecord:
    step: int  # 步骤数
    action: str  # 动作
    thought: str  # 思考
    tool_name: str | None  # 工具名称
    tool_arguments: dict[str, Any]  # 工具参数
    raw_output: str  # 原始输出
    model_name: str  # 模型名称


@dataclass(slots=True)
class AgentState:
    run_id: str  # 运行 ID
    user_id: str  # 用户 ID
    task: str  # 任务 ID
    session_id: str  # 会话 ID
    status: RunStatus = RunStatus.IDLE  # 运行状态
    step: int = 0  # 步骤数
    started_at: float = field(default_factory=time.time)  # 开始时间
    updated_at: float = field(default_factory=time.time)  # 更新时间
    history: list[StepRecord] = field(default_factory=list)  # 步骤历史
    scratchpad: list[str] = field(default_factory=list)  # 临时存储
    tool_results: list[ToolResult] = field(default_factory=list)  # 工具结果
    final_output: str | None = None  # 最终输出
    failure_reason: str | None = None  # 失败原因
    conversation: list[SessionMessage] = field(default_factory=list)  # 会话消息
    metadata: dict[str, Any] = field(default_factory=dict)  # 元数据


# =========================
# 工具：事件总线
# =========================

class EventSubscriber(Protocol):
    """
    事件订阅者
    """
    def handle(self, event: AgentEvent) -> None:
        ...


class EventBus:
    """
    事件总线
    """
    def __init__(self) -> None:
        self._subscribers: list[EventSubscriber] = []

    def subscribe(self, subscriber: EventSubscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

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


# =========================
# 工具：持久化
# =========================

def ensure_dirs() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


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
        path = self._path(session_id)
        path.write_text(
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
            "scratchpad": list(state.scratchpad),
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
# LLM 封装
# =========================

class LLMClient:
    def __init__(self, llm: Any, model_name: str) -> None:
        self.llm = llm
        self.model_name = model_name

    def decide(self, prompt: str) -> str:
        response = self.llm.invoke(prompt)
        return getattr(response, "content", str(response))


# =========================
# 输出解析
# =========================

class OutputParser:
    ACTIONS = {"think", "tool", "final", "ask_human"}

    def parse(self, raw_output: str) -> ModelDecision:
        text = raw_output.strip()

        # 去掉 markdown code fence
        text = re.sub(r"^\s*```json\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*```\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

        # 提取第一个 JSON 对象
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            # 兜底：当成 final 处理，避免整个 loop 崩掉
            return ModelDecision(action="final", answer=text, thought=text)

        candidate = match.group(0)

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            candidate = re.sub(r",\s*}", "}", candidate)
            candidate = re.sub(r",\s*]", "]", candidate)
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                return ModelDecision(action="final", answer=text, thought=text)

        action = str(data.get("action", "final")).strip()
        if action not in self.ACTIONS:
            action = "final"

        thought = str(data.get("thought", "") or "")
        tool_name = data.get("tool_name")
        tool_arguments = data.get("tool_arguments") or {}
        answer = data.get("answer")
        reason = data.get("reason")

        if action == "tool" and not tool_name:
            return ModelDecision(action="final", answer=text, thought=text)

        if not isinstance(tool_arguments, dict):
            tool_arguments = {}

        return ModelDecision(
            action=action,
            thought=thought,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            answer=answer,
            reason=reason,
        )


# =========================
# 安全与预算
# =========================

class BudgetExceeded(Exception):
    pass


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


class SemanticLoopDetector:
    """
    语义循环检测器
    """

    def is_looping(self, state: AgentState, decision: ModelDecision) -> bool:
        sig = {
            "action": decision.action,
            "tool_name": decision.tool_name,
            "tool_arguments": decision.tool_arguments,
            "thought": (decision.thought or "").strip()[:120],
        }

        signatures = state.metadata.get("decision_signatures", [])
        signatures.append(sig)
        signatures = signatures[-4:]
        state.metadata["decision_signatures"] = signatures

        if len(signatures) >= 3:
            last3 = signatures[-3:]
            if last3[0] == last3[1] == last3[2]:
                return True

        if len(state.history) >= 3:
            last_actions = [h.action for h in state.history[-3:]]
            if last_actions == ["think", "think", "think"]:
                return True

        return False


class GuardrailEngine:
    """
    守卫引擎
    """
    def validate_user_input(self, task: str) -> None:
        if not task.strip():
            raise ValueError("用户输入不能为空")

    def validate_tool_name(self, tool_name: str, registry: "ToolRegistry") -> None:
        if registry.get(tool_name) is None:
            raise ValueError(f"未知工具: {tool_name}")

    def validate_final_output(self, text: str | None) -> None:
        if text is None or not str(text).strip():
            raise ValueError("最终输出不能为空")


# =========================
# 工具系统
# =========================

class BaseTool(Protocol):
    """
    基础工具接口
    """
    name: str
    description: str

    def run(self, arguments: dict[str, Any]) -> str:
        ...


class ToolRegistry:
    """
    工具注册器
    """
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """
        获取工具
        """
        return self._tools.get(name)

    def describe(self) -> list[dict[str, str]]:
        """
        描述所有工具
        """
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self._tools.values()
        ]


class ToolExecutor:
    """
    工具执行器
    """
    def __init__(self, registry: ToolRegistry, event_bus: EventBus) -> None:
        self.registry = registry
        self.event_bus = event_bus

    def execute(self, state: AgentState, call: ToolCall) -> ToolResult:
        """
        执行工具调用
        """
        tool = self.registry.get(call.tool_name)
        if tool is None:
            raise ValueError(f"tool not found: {call.tool_name}")
        """
        发布工具开始事件
        """
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
            """
            执行工具调用
            """
            output = tool.run(call.arguments)
            result = ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                output=output,
                success=True,
                latency_ms=int((time.time() - start) * 1000),
                error=None,
            )
            """
            发布工具完成事件
            """
            self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.TOOL_FINISHED,
                    ts=time.time(),
                    step=state.step,
                    payload={"tool_name": call.tool_name, "output": output},
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
            """
            发布工具失败事件
            """
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

class GetCurrentTimeTool:
    name = "get_current_time"
    description = (
        "获取当前本地时间。"
        "参数: {}。"
        "适合回答现在几点、当前时间、今天日期。"
    )

    def run(self, arguments: dict[str, Any]) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class CalculatorTool:
    name = "calculator"
    description = (
        "做基础数学运算。"
        '参数: {"expression": "((2+3)*4)/5"}。'
        "只支持数字和 + - * / // % ** ()。"
    )

    def run(self, arguments: dict[str, Any]) -> str:
        expr = arguments.get("expression")
        if not isinstance(expr, str) or not expr.strip():
            raise ValueError("calculator 需要字符串参数 expression")
        value = safe_eval(expr)
        return str(value)


def safe_eval(expression: str) -> float | int:
    """
    安全评估表达式
    """
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

    return eval(compile(tree, filename="<expr>", mode="eval"), {"__builtins__": {}}, {})


# =========================
# 上下文构造
# =========================

class ContextBuilder:
    def __init__(self, short_memory_turns: int = SHORT_MEMORY_TURNS) -> None:
        self.short_memory_turns = short_memory_turns

    def build(self, state: AgentState, tools: list[dict[str, str]]) -> str:
        recent_messages = state.conversation[-self.short_memory_turns :]
        recent_tools = state.tool_results[-3:]
        recent_scratchpad = state.scratchpad[-4:]

        conversation_text = "\n".join(
            f"{m.role}: {m.content}" for m in recent_messages
        ) or "(无)"

        tool_result_text = "\n".join(
            f"- {r.tool_name}: success={r.success}, output={r.output or r.error}"
            for r in recent_tools
        ) or "(无)"

        scratchpad_text = "\n".join(f"- {s}" for s in recent_scratchpad) or "(无)"

        tool_desc = json.dumps(tools, ensure_ascii=False, indent=2)

        return f"""
你是一个可以多步决策的中文智能助手。
你必须严格按照要求输出一个 JSON 对象，不要输出 markdown，不要输出解释，不要输出代码块。

你的目标：
- 优先基于已有对话上下文回答问题
- 只有在确实需要外部能力时才调用工具
- 当已有足够信息时，立即 final
- think 可以用来做一次中间思考，但不要无限 think
- 工具失败后，可以尝试换策略；如果无法继续，可以 ask_human 或 final

你可用的工具:
{tool_desc}

允许的 action:
1. think
2. tool
3. final
4. ask_human

输出 JSON 格式:
{{
  "action": "think | tool | final | ask_human",
  "thought": "你的思考，简洁",
  "tool_name": "当 action=tool 时必填",
  "tool_arguments": {{}},
  "answer": "当 action=final 时必填",
  "reason": "当 action=ask_human 时可填"
}}

规则:
- 只能输出一个 JSON 对象
- 不要出现 markdown 代码块
- 如果 action=tool，必须提供 tool_name 和 tool_arguments
- 如果 action=final，必须提供 answer
- 看到“现在几点”“帮我算一下”这类请求时，优先使用工具
- 你已经拥有短时记忆，不要重复问用户刚刚说过的话
- 不要反复调用同一个工具做同样的事情

当前任务:
{state.task}

短时记忆（最近对话）:
{conversation_text}

最近工具结果:
{tool_result_text}

最近 scratchpad:
{scratchpad_text}

请只输出 JSON:
""".strip()


# =========================
# Agent Runtime
# =========================

class AgentRuntime:
    def __init__(
        self,
        llm_client: LLMClient,
        context_builder: ContextBuilder,
        output_parser: OutputParser,
        tool_executor: ToolExecutor,
        guardrail_engine: GuardrailEngine,
        checkpoint_store: FileCheckpointStore,
        event_bus: EventBus,
        budget_controller: BudgetController,
        loop_detector: SemanticLoopDetector,
        registry: ToolRegistry,
        session_store: FileSessionStore,
    ) -> None:
        self.llm_client = llm_client
        self.context_builder = context_builder
        self.output_parser = output_parser
        self.tool_executor = tool_executor
        self.guardrail_engine = guardrail_engine
        self.checkpoint_store = checkpoint_store
        self.event_bus = event_bus
        self.budget_controller = budget_controller
        self.loop_detector = loop_detector
        self.registry = registry
        self.session_store = session_store

    def chat(self, user_id: str, session_id: str, message: str) -> AgentState:
        """
        处理用户输入，更新会话历史，调用模型，执行工具，更新状态。
        :param user_id: 用户 ID
        :param session_id: 会话 ID
        :param message: 用户输入
        :return: 更新后的状态
        """
        # 验证用户输入
        self.guardrail_engine.validate_user_input(message)
        # 加载会话历史
        previous_messages = self.session_store.load_messages(session_id)
        # 追加用户消息
        self.session_store.append_message(session_id, "user", message)

        # 构建当前对话
        current_conversation = previous_messages + [SessionMessage(role="user", content=message)]
        # 初始化状态状态
        state = AgentState(
            run_id=str(uuid.uuid4()),
            user_id=user_id,
            task=message,
            session_id=session_id,
            status=RunStatus.RUNNING,
            conversation=current_conversation,
        )
        # 发布运行开始事件
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
                # 更新状态
                state.updated_at = time.time()
                # 检查预算
                self.budget_controller.check(state)
                # 发布步骤开始事件
                self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.STEP_STARTED,
                        ts=time.time(),
                        step=state.step,
                    )
                )
                # 
                prompt = self.context_builder.build(state, self.registry.describe())
                raw_output = self.llm_client.decide(prompt)
                # 发布模型输出事件
                self.event_bus.publish(
                    AgentEvent(
                        run_id=state.run_id,
                        event_type=EventType.MODEL_OUTPUT,
                        ts=time.time(),
                        step=state.step,
                        payload={"raw_output": raw_output},
                    )
                )
                # 解析模型输出
                decision = self.output_parser.parse(raw_output)
                # 记录步骤
                step_record = StepRecord(
                    step=state.step,
                    action=decision.action,
                    thought=decision.thought,
                    tool_name=decision.tool_name,
                    tool_arguments=decision.tool_arguments,
                    raw_output=raw_output,
                    model_name=self.llm_client.model_name,
                )
                # 记录步骤
                state.history.append(step_record)
                # 检查循环
                if self.loop_detector.is_looping(state, decision):
                    state.status = RunStatus.DEGRADED
                    state.failure_reason = "检测到重复决策循环"
                    state.final_output = self._build_recovery_answer(state)
                    self.guardrail_engine.validate_final_output(state.final_output)
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
                    self.session_store.append_message(session_id, "assistant", state.final_output)
                    return state
                # 如果是 think，记录思考
                if decision.action == "think":
                    state.scratchpad.append(decision.thought or "")
                    self._save_checkpoint(state)
                    self.event_bus.publish(
                        AgentEvent(
                            run_id=state.run_id,
                            event_type=EventType.STEP_FINISHED,
                            ts=time.time(),
                            step=state.step,
                            payload={"action": "think"},
                        )
                    )
                    state.step += 1
                    continue
                # 如果是 tool，调用工具执行
                if decision.action == "tool":
                    assert decision.tool_name is not None
                    self.guardrail_engine.validate_tool_name(decision.tool_name, self.registry)

                    self.event_bus.publish(
                        AgentEvent(
                            run_id=state.run_id,
                            event_type=EventType.TOOL_SELECTED,
                            ts=time.time(),
                            step=state.step,
                            payload={
                                "tool_name": decision.tool_name,
                                "arguments": decision.tool_arguments,
                            },
                        )
                    )

                    tool_call = ToolCall(
                        call_id=str(uuid.uuid4()),
                        tool_name=decision.tool_name,
                        arguments=decision.tool_arguments,
                        idempotency_key=(
                            f"{state.run_id}:{decision.tool_name}:"
                            f"{json.dumps(decision.tool_arguments, ensure_ascii=False, sort_keys=True)}"
                        ),
                    )
                    result = self.tool_executor.execute(state, tool_call)
                    state.tool_results.append(result)

                    self._save_checkpoint(state)
                    self.event_bus.publish(
                        AgentEvent(
                            run_id=state.run_id,
                            event_type=EventType.STEP_FINISHED,
                            ts=time.time(),
                            step=state.step,
                            payload={"action": "tool", "tool_name": decision.tool_name},
                        )
                    )
                    state.step += 1
                    continue
                # 如果是 ask_human，等待人工介入
                if decision.action == "ask_human":
                    state.status = RunStatus.WAITING_HUMAN
                    state.failure_reason = decision.reason or "模型请求人工介入"
                    self._save_checkpoint(state)
                    self.event_bus.publish(
                        AgentEvent(
                            run_id=state.run_id,
                            event_type=EventType.HUMAN_REQUIRED,
                            ts=time.time(),
                            step=state.step,
                            payload={"reason": state.failure_reason},
                        )
                    )
                    return state

                # final
                state.final_output = decision.answer or decision.thought or "任务完成"
                self.guardrail_engine.validate_final_output(state.final_output)
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
        # 如果预算超支
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
        # 如果发生其他异常
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
    # 保存检查点
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
    # 构建恢复答复
    def _build_recovery_answer(self, state: AgentState) -> str:
        if state.tool_results:
            last = state.tool_results[-1]
            if last.success:
                return (
                    "本次运行提前收敛，下面给出基于最近一次工具结果的答复：\n"
                    f"{last.output}"
                )
            return (
                "本次运行提前收敛，最近一次工具调用失败。\n"
                f"工具: {last.tool_name}\n"
                f"错误: {last.error}"
            )

        if state.scratchpad:
            return (
                "本次运行提前收敛，下面给出当前可提供的结果：\n"
                f"{state.scratchpad[-1]}"
            )

        return "本次运行提前结束，暂时没有得到足够稳定的结果。"


# =========================
# 组装
# =========================
# 构建运行时
def build_runtime() -> AgentRuntime:
    ensure_dirs()

    loader = create_model_loader(
        model_url=MODEL_URL,
        model_name=MODEL_NAME,
        api_key=API_KEY,
        temperature=TEMPERATURE,
    )
    llm = loader.load()

    llm_client = LLMClient(llm=llm, model_name=MODEL_NAME)

    event_bus = EventBus()
    event_bus.subscribe(LoggingSubscriber())

    registry = ToolRegistry()
    registry.register(GetCurrentTimeTool())
    registry.register(CalculatorTool())

    runtime = AgentRuntime(
        llm_client=llm_client,
        context_builder=ContextBuilder(short_memory_turns=SHORT_MEMORY_TURNS),
        output_parser=OutputParser(),
        tool_executor=ToolExecutor(registry=registry, event_bus=event_bus),
        guardrail_engine=GuardrailEngine(),
        checkpoint_store=FileCheckpointStore(CHECKPOINT_DIR),
        event_bus=event_bus,
        budget_controller=BudgetController(
            max_steps=MAX_STEPS,
            max_tool_calls=MAX_TOOL_CALLS,
            max_seconds=MAX_SECONDS,
        ),
        loop_detector=SemanticLoopDetector(),
        registry=registry,
        session_store=FileSessionStore(SESSION_DIR),
    )
    return runtime


# =========================
# CLI Demo
# =========================

def main() -> None:
    runtime = build_runtime()

    user_id = "demo_user"
    session_id = "demo_session"

    print("工业 Agent Loop MVP 已启动。")
    print("输入 exit 退出。")
    print("你可以试试：")
    print("- 你好，我叫小王")
    print("- 你还记得我叫什么吗")
    print("- 现在几点")
    print("- 帮我计算 ((12+8)*3)/5")
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