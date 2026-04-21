from __future__ import annotations

import ast
import time
from typing import Any

from packages.model_loader import create_model_loader
from packages.tools import (
    BashSessionManager,
    BashTool as CoreBashTool,
    FileEditTool as CoreFileEditTool,
    FileReadTool as CoreFileReadTool,
    FileWriteTool as CoreFileWriteTool,
    GlobTool as CoreGlobTool,
    GrepTool as CoreGrepTool,
    MCPTool as CoreMCPTool,
    SkillTool as CoreSkillTool,
    ToolSearchTool as CoreToolSearchTool,
    WebFetchTool as CoreWebFetchTool,
    WebSearchTool as CoreWebSearchTool,
)

from .agent_loop import AgentRuntime
from .budget import BudgetController, IdempotencyStore, RetryPolicy
from packages.memory.manager import MemoryManager
from packages.memory.store import FileMemoryStore
from packages.memory.summarizer import TranscriptSummarizer
from packages.memory.compaction import CompactionPipeline
from .config import (
    API_KEY,
    CHECKPOINT_DIR,
    MAX_SECONDS,
    MAX_STEPS,
    MAX_TOOL_CALLS,
    MEMORY_TRANSCRIPTS_DIR,
    MEMORY_USERS_DIR,
    MODEL_NAME,
    MODEL_URL,
    SESSION_DIR,
    SHORT_MEMORY_TURNS,
    TEMPERATURE,
    TOOL_CATALOG,
    WORKSPACE_DIR,
)
from .events import AuditSubscriber, EventBus, LoggingSubscriber
from .executor import ToolExecutor
from .guardrail import GuardrailEngine, workspace_resolve
from .llm_client import NativeToolCallingLLMClient
from .message_builder import MessageBuilder
from .models import ToolSpec
from .permission import ApprovalController, PermissionController
from .registry import ToolRegistry
from .store import FileCheckpointStore, FileSessionStore, ensure_dirs


class GetCurrentTimeTool:
    """
    获取当前时间工具
    """
    async def arun(self, arguments: dict[str, Any]) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class CalculatorTool:
    """
    计算工具
    """
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


class ListDirTool:
    """
    列出目录工具
    """
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
            description="Get current local time.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        GetCurrentTimeTool(),
    )
    registry.register(
        ToolSpec(
            name="calculator",
            description="Evaluate a basic math expression.",
            parameters={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        ),
        CalculatorTool(),
    )
    registry.register(
        ToolSpec(
            name="tool_search",
            description="Discover available tools and recommend which ones should be loaded next.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["search", "list"]},
                    "intent": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "current_loaded_tools": {"type": "array", "items": {"type": "string"}},
                    "only_not_loaded": {"type": "boolean"},
                    "max_results": {"type": "integer"},
                },
                "required": [],
            },
        ),
        CoreToolSearchTool(catalog=TOOL_CATALOG),
    )
    registry.register(
        ToolSpec(
            name="file_read",
            description="Read a UTF-8 text file or a line range from the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "max_bytes": {"type": "integer"},
                },
                "required": ["path"],
            },
            risk_level="medium",
            side_effect="local_fs",
            sandbox_required=True,
        ),
        CoreFileReadTool(workspace_root=WORKSPACE_DIR),
    )
    registry.register(
        ToolSpec(
            name="file_write",
            description="Create, overwrite, or append a text file in the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "mode": {"type": "string", "enum": ["create", "overwrite", "append"]},
                },
                "required": ["path", "content"],
            },
            readonly=False,
            risk_level="high",
            side_effect="local_fs",
            sandbox_required=True,
            writes_workspace=True,
            max_retries=0,
            approval_policy="always",
        ),
        CoreFileWriteTool(workspace_root=WORKSPACE_DIR),
    )
    registry.register(
        ToolSpec(
            name="file_edit",
            description="Replace exact text or insert text at a specific line in a workspace file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "action": {"type": "string", "enum": ["replace_exact", "insert_at_line"]},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                    "expected_occurrences": {"type": "integer"},
                    "line_no": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["path", "action"],
            },
            readonly=False,
            risk_level="high",
            side_effect="local_fs",
            sandbox_required=True,
            writes_workspace=True,
            cache_policy="none",
            max_retries=0,
            approval_policy="always",
        ),
        CoreFileEditTool(workspace_root=WORKSPACE_DIR),
    )
    registry.register(
        ToolSpec(
            name="list_dir",
            description="List files and directories inside workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
                "required": [],
            },
            risk_level="medium",
            side_effect="local_fs",
            sandbox_required=True,
        ),
        ListDirTool(),
    )
    registry.register(
        ToolSpec(
            name="glob",
            description="Find files inside the workspace using a glob pattern.",
            parameters={
                "type": "object",
                "properties": {
                    "base_path": {"type": "string", "default": "."},
                    "pattern": {"type": "string"},
                    "include_hidden": {"type": "boolean"},
                    "max_results": {"type": "integer"},
                },
                "required": ["pattern"],
            },
            risk_level="medium",
            side_effect="local_fs",
            sandbox_required=True,
        ),
        CoreGlobTool(workspace_root=WORKSPACE_DIR),
    )
    registry.register(
        ToolSpec(
            name="grep",
            description="Search file contents inside the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "base_path": {"type": "string", "default": "."},
                    "pattern": {"type": "string"},
                    "is_regex": {"type": "boolean", "default": True},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "file_glob": {"type": "string"},
                    "max_matches": {"type": "integer"},
                },
                "required": ["pattern"],
            },
            risk_level="medium",
            side_effect="local_fs",
            sandbox_required=True,
        ),
        CoreGrepTool(workspace_root=WORKSPACE_DIR),
    )
    registry.register(
        ToolSpec(
            name="web_search",
            description="Search the web using Tavily first, SerpAPI second, and HTML fallback, with concurrent fetching.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "queries": {"type": "array", "items": {"type": "string"}},
                    "include_snippets": {"type": "boolean", "default": True},
                    "include_page_content": {"type": "boolean", "default": True},
                },
            },
            risk_level="medium",
            side_effect="network",
            network_required=True,
            cache_policy="none",
        ),
        CoreWebSearchTool(),
    )
    registry.register(
        ToolSpec(
            name="web_fetch",
            description="Fetch one or more URLs concurrently and extract simplified page text.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "urls": {"type": "array", "items": {"type": "string"}},
                },
            },
            risk_level="medium",
            side_effect="network",
            network_required=True,
            cache_policy="none",
        ),
        CoreWebFetchTool(),
    )
    registry.register(
        ToolSpec(
            name="skill",
            description="Discover, inspect, and read local skills.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "inspect", "read"]},
                    "query": {"type": "string"},
                    "skill_name": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": [],
            },
            side_effect="local_fs",
            sandbox_required=True,
        ),
        CoreSkillTool(workspace_root=WORKSPACE_DIR),
    )
    registry.register(
        ToolSpec(
            name="mcp",
            description="Inspect locally configured MCP servers, resources, prompts, and tools.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list_servers", "show_config", "inspect_server", "list_resources", "read_resource"],
                    },
                    "config_path": {"type": "string"},
                    "server": {"type": "string"},
                    "uri": {"type": "string"},
                },
                "required": [],
            },
            side_effect="local_fs",
            sandbox_required=True,
        ),
        CoreMCPTool(workspace_root=WORKSPACE_DIR),
    )
    registry.register(
        ToolSpec(
            name="bash",
            description="Run a bash or PowerShell command in a persistent workspace session.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "session_id": {"type": "string", "default": "default"},
                    "cwd": {"type": "string"},
                    "timeout_s": {"type": "number", "default": 20},
                    "restart": {"type": "boolean", "default": False},
                },
                "required": ["command"],
            },
            readonly=False,
            risk_level="high",
            side_effect="shell",
            sandbox_required=True,
            writes_workspace=True,
            cache_policy="none",
            max_retries=0,
            approval_policy="always",
        ),
        CoreBashTool(session_manager=BashSessionManager(workspace_root=WORKSPACE_DIR)),
    )

    event_bus = EventBus()
    event_bus.subscribe(LoggingSubscriber())
    event_bus.subscribe(AuditSubscriber())
    guardrail_engine = GuardrailEngine()
    idempotency_store = IdempotencyStore()
    tool_executor = ToolExecutor(
        registry=registry,
        permission_controller=PermissionController(lambda user_id: "user"),
        approval_controller=ApprovalController(),
        guardrail_engine=guardrail_engine,
        retry_policy=RetryPolicy(max_retries=2, base_delay=0.3),
        idempotency_store=idempotency_store,
        event_bus=event_bus,
    )
    runtime = AgentRuntime(
        llm_client=NativeToolCallingLLMClient(llm=llm, model_name=MODEL_NAME),
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
    runtime.memory_manager = MemoryManager(
        store=FileMemoryStore(MEMORY_USERS_DIR),
        workspace_id=str(WORKSPACE_DIR.resolve()),
    )
    runtime.compaction_pipeline = CompactionPipeline(
        summarizer=TranscriptSummarizer(llm=llm),
        archive_dir=MEMORY_TRANSCRIPTS_DIR,
    )
    return runtime
