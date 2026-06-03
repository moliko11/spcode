from __future__ import annotations

import ast
import os
import time
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

from packages.model_loader import create_model_loader
from packages.tools import (
    BashSessionManager,
    BashTool as CoreBashTool,
    EnterPlanModeTool as CoreEnterPlanModeTool,
    ExitPlanModeTool as CoreExitPlanModeTool,
    FileEditTool as CoreFileEditTool,
    FileReadTool as CoreFileReadTool,
    FileWriteTool as CoreFileWriteTool,
    FindSymbolTool as CoreFindSymbolTool,
    GitDiffTool as CoreGitDiffTool,
    GitLogTool as CoreGitLogTool,
    GlobTool as CoreGlobTool,
    GrepTool as CoreGrepTool,
    LintTool as CoreLintTool,
    MCPTool as CoreMCPTool,
    RunTestsTool as CoreRunTestsTool,
    SkillTool as CoreSkillTool,
    TaskCreateTool as CoreTaskCreateTool,
    TaskListTool as CoreTaskListTool,
    TaskOutputTool as CoreTaskOutputTool,
    TaskReplanTool as CoreTaskReplanTool,
    TaskStopTool as CoreTaskStopTool,
    TaskUpdateTool as CoreTaskUpdateTool,
    TaskVerifyTool as CoreTaskVerifyTool,
    TodoWriteTool as CoreTodoWriteTool,
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
    CHECKPOINT_DIR,
    MEMORY_TRANSCRIPTS_DIR,
    MEMORY_USERS_DIR,
    PLAN_RUNS_DIR,
    PLANS_DIR,
    SESSION_DIR,
    TOOL_CATALOG,
    WORKFLOWS_DIR,
    load_runtime_config,
)
from .events import AuditSubscriber, EventBus, LoggingSubscriber
from .executor import ToolExecutor
from .guardrail import GuardrailEngine, resolve_workspace_path
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
    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    async def arun(self, arguments: dict[str, Any]) -> str:
        root = resolve_workspace_path(self.workspace_root, str(arguments.get("path", ".")))
        if not root.exists():
            raise FileNotFoundError(f"directory not found: {root.name}")
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {root.name}")
        entries = []
        for child in sorted(root.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            kind = "dir" if child.is_dir() else "file"
            entries.append(f"{kind}\t{child.resolve().relative_to(self.workspace_root)}")
        return "\n".join(entries)


def build_runtime(
    workspace_root: str | Path | None = None,
    config_path: str | Path | None = None,
    max_tool_calls: int | None = None,
    max_state_tool_calls: int | None = None,
    max_read_tool_calls: int | None = None,
    max_network_tool_calls: int | None = None,
    max_high_risk_tool_calls: int | None = None,
    enable_event_logging: bool | None = None,
) -> AgentRuntime:
    ensure_dirs()
    runtime_config = load_runtime_config(config_path)
    workspace_dir = Path(workspace_root).resolve() if workspace_root is not None else runtime_config.workspace_root.resolve()
    skill_roots = [Path(root).resolve() for root in runtime_config.skill_roots]
    if enable_event_logging is None:
        enable_event_logging = os.getenv("AGENT_EVENT_STDOUT", "0").lower() in {"1", "true", "yes", "on"}
    loader = create_model_loader(
        model_url=runtime_config.model_url,
        model_name=runtime_config.model_name,
        api_key=runtime_config.api_key,
        temperature=runtime_config.temperature,
    )
    llm = loader.load()
    active_model_name = getattr(loader, "active_model_name", runtime_config.model_name) or runtime_config.model_name

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="get_current_time",
            description="Get current local time.",
            parameters={"type": "object", "properties": {}, "required": []},
            category="utility",
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
            category="utility",
        ),
        CalculatorTool(),
    )
    registry.register(
        ToolSpec(
            name="enter_plan_mode",
            description="Enter a side-effect-free planning mode. While active, write, shell, network, and external side-effect tools are blocked.",
            parameters={
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "reason": {"type": "string"},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "plan_mode_session_id": {"type": "string"},
                },
                "required": ["goal", "reason"],
            },
            side_effect="none",
            category="workflow",
            cache_policy="none",
        ),
        CoreEnterPlanModeTool(),
    )
    registry.register(
        ToolSpec(
            name="exit_plan_mode",
            description="Exit planning mode with a decision: approved, rejected, or revise_required.",
            parameters={
                "type": "object",
                "properties": {
                    "plan_mode_session_id": {"type": "string"},
                    "plan_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["approved", "rejected", "revise_required"]},
                    "notes": {"type": "string"},
                },
                "required": ["decision"],
            },
            side_effect="none",
            category="workflow",
            cache_policy="none",
        ),
        CoreExitPlanModeTool(),
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
            category="meta",
        ),
        CoreToolSearchTool(catalog=TOOL_CATALOG),
    )
    registry.register(
        ToolSpec(
            name="todo_write",
            description="Create or update a lightweight visible todo list backed by WorkflowStore.",
            parameters={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "plan_id": {"type": "string"},
                    "goal": {"type": "string"},
                    "context": {"type": "string"},
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "task_id": {"type": "string"},
                                "content": {"type": "string"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "running", "completed", "blocked", "cancelled", "skipped"]},
                                "priority": {"type": "string"},
                                "linked_step_id": {"type": "string"},
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
            side_effect="local_fs",
            category="workflow",
            sandbox_required=True,
            cache_policy="none",
        ),
        CoreTodoWriteTool(plans_dir=PLANS_DIR, plan_runs_dir=PLAN_RUNS_DIR, workflows_dir=WORKFLOWS_DIR),
    )
    registry.register(
        ToolSpec(
            name="task_create",
            description="Create a workflow task inside a persisted workflow, or create a new ad-hoc workflow with one task.",
            parameters={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "plan_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "goal": {"type": "string"},
                    "context": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "suggested_tools": {"type": "array", "items": {"type": "string"}},
                    "target_files": {"type": "array", "items": {"type": "string"}},
                    "artifacts": {"type": "array", "items": {"type": "object"}},
                    "evidence": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["title"],
            },
            side_effect="local_fs",
            category="workflow",
            sandbox_required=True,
            cache_policy="none",
        ),
        CoreTaskCreateTool(plans_dir=PLANS_DIR, plan_runs_dir=PLAN_RUNS_DIR, workflows_dir=WORKFLOWS_DIR),
    )
    registry.register(
        ToolSpec(
            name="task_update",
            description="Update a persisted workflow task status, result summary, evidence, artifacts, dependencies, or metadata.",
            parameters={
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "plan_run_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "ready", "running", "waiting_human", "completed", "failed", "skipped", "blocked", "cancelled"]},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "result_summary": {"type": "string"},
                    "error": {"type": "string"},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "target_files": {"type": "array", "items": {"type": "string"}},
                    "artifacts": {"type": "array", "items": {"type": "object"}},
                    "evidence": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["task_id"],
            },
            side_effect="local_fs",
            category="workflow",
            sandbox_required=True,
            cache_policy="none",
        ),
        CoreTaskUpdateTool(plans_dir=PLANS_DIR, plan_runs_dir=PLAN_RUNS_DIR, workflows_dir=WORKFLOWS_DIR),
    )
    registry.register(
        ToolSpec(
            name="task_list",
            description="List persisted workflow tasks from a workflow, a plan run, or recent workflows.",
            parameters={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "plan_id": {"type": "string"},
                    "plan_run_id": {"type": "string"},
                    "status_filter": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
            side_effect="local_fs",
            category="workflow",
            sandbox_required=True,
        ),
        CoreTaskListTool(plans_dir=PLANS_DIR, plan_runs_dir=PLAN_RUNS_DIR, workflows_dir=WORKFLOWS_DIR),
    )
    registry.register(
        ToolSpec(
            name="task_output",
            description="Read task, workflow, or plan run output including summaries, artifacts, and evidence.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "plan_id": {"type": "string"},
                    "plan_run_id": {"type": "string"},
                },
                "required": [],
            },
            side_effect="local_fs",
            category="workflow",
            sandbox_required=True,
        ),
        CoreTaskOutputTool(plans_dir=PLANS_DIR, plan_runs_dir=PLAN_RUNS_DIR, workflows_dir=WORKFLOWS_DIR),
    )
    registry.register(
        ToolSpec(
            name="task_verify",
            description="Verify a workflow task using acceptance criteria and optional test-command evidence.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "plan_id": {"type": "string"},
                    "result_summary": {"type": "string"},
                    "test_command": {"type": "string"},
                    "timeout_s": {"type": "integer"},
                },
                "required": ["task_id"],
            },
            side_effect="local_fs",
            category="workflow",
            sandbox_required=True,
            cache_policy="none",
        ),
        CoreTaskVerifyTool(plans_dir=PLANS_DIR, plan_runs_dir=PLAN_RUNS_DIR, workflows_dir=WORKFLOWS_DIR, workspace_root=workspace_dir),
    )
    registry.register(
        ToolSpec(
            name="task_replan",
            description="Append or replace follow-up workflow tasks after a failed task.",
            parameters={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "plan_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "failed_task_id": {"type": "string"},
                    "strategy": {"type": "string", "enum": ["append", "replace"]},
                    "reason": {"type": "string"},
                    "new_tasks": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["workflow_id", "new_tasks"],
            },
            side_effect="local_fs",
            category="workflow",
            sandbox_required=True,
            cache_policy="none",
        ),
        CoreTaskReplanTool(plans_dir=PLANS_DIR, plan_runs_dir=PLAN_RUNS_DIR, workflows_dir=WORKFLOWS_DIR),
    )
    registry.register(
        ToolSpec(
            name="task_stop",
            description="Stop a workflow task, all unfinished tasks in a workflow, or a running plan run.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "plan_id": {"type": "string"},
                    "plan_run_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": [],
            },
            readonly=False,
            risk_level="medium",
            side_effect="local_fs",
            category="workflow",
            sandbox_required=True,
            cache_policy="none",
        ),
        CoreTaskStopTool(plans_dir=PLANS_DIR, plan_runs_dir=PLAN_RUNS_DIR, workflows_dir=WORKFLOWS_DIR),
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
            category="workspace",
            sandbox_required=True,
        ),
        CoreFileReadTool(workspace_root=workspace_dir, extra_roots=skill_roots),
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
            category="workspace",
            sandbox_required=True,
            writes_workspace=True,
            max_retries=0,
            approval_policy="always",
        ),
        CoreFileWriteTool(workspace_root=workspace_dir),
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
            category="workspace",
            sandbox_required=True,
            writes_workspace=True,
            cache_policy="none",
            max_retries=0,
            approval_policy="always",
        ),
        CoreFileEditTool(workspace_root=workspace_dir),
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
            category="workspace",
            sandbox_required=True,
        ),
        ListDirTool(workspace_root=workspace_dir),
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
            category="workspace",
            sandbox_required=True,
        ),
        CoreGlobTool(workspace_root=workspace_dir),
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
            category="workspace",
            sandbox_required=True,
        ),
        CoreGrepTool(workspace_root=workspace_dir),
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
                    "include_page_content": {"type": "boolean", "default": False},
                },
            },
            risk_level="medium",
            side_effect="network",
            category="web",
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
            category="web",
            network_required=True,
            cache_policy="none",
        ),
        CoreWebFetchTool(),
    )
    registry.register(
        ToolSpec(
            name="skill",
            description="Discover, inspect, read, invoke, and check dependencies of local skills.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "inspect", "read", "invoke", "list_files", "check_deps"],
                    },
                    "query": {"type": "string"},
                    "skill_name": {"type": "string"},
                    "path": {"type": "string"},
                    "arguments": {
                        "type": "string",
                        "description": "Arguments to pass when invoking a skill ($ARGUMENTS placeholder).",
                    },
                    "session_id": {"type": "string"},
                },
                "required": [],
            },
            side_effect="local_fs",
            category="meta",
            sandbox_required=True,
        ),
        CoreSkillTool(workspace_root=workspace_dir, skill_roots=skill_roots),
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
            category="integration",
            sandbox_required=True,
        ),
        CoreMCPTool(workspace_root=workspace_dir),
    )
    registry.register(
        ToolSpec(
            name="bash",
            description="Run a PowerShell command in a persistent workspace session.",
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
            category="execution",
            sandbox_required=True,
            writes_workspace=True,
            cache_policy="none",
            max_retries=0,
            approval_policy="always",
        ),
        CoreBashTool(session_manager=BashSessionManager(workspace_root=workspace_dir)),
    )
    registry.register(
        ToolSpec(
            name="run_tests",
            description="Run the project test suite (pytest) and return pass/fail results.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "args": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": [],
            },
            category="code",
        ),
        CoreRunTestsTool(workspace_root=workspace_dir),
    )
    registry.register(
        ToolSpec(
            name="lint",
            description="Run ruff (or pyflakes) on workspace code and return lint issues.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "fix": {"type": "boolean"},
                    "timeout": {"type": "integer"},
                },
                "required": [],
            },
            readonly=False,
            risk_level="medium",
            category="code",
        ),
        CoreLintTool(workspace_root=workspace_dir),
    )
    registry.register(
        ToolSpec(
            name="git_diff",
            description="Show git diff for workspace changes, staged files, or between commits.",
            parameters={
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean"},
                    "commit": {"type": "string"},
                    "path": {"type": "string"},
                    "stat": {"type": "boolean"},
                    "timeout": {"type": "integer"},
                },
                "required": [],
            },
            category="code",
        ),
        CoreGitDiffTool(workspace_root=workspace_dir),
    )
    registry.register(
        ToolSpec(
            name="git_log",
            description="Show git commit history to understand code evolution.",
            parameters={
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "path": {"type": "string"},
                    "author": {"type": "string"},
                    "since": {"type": "string"},
                    "oneline": {"type": "boolean"},
                    "timeout": {"type": "integer"},
                },
                "required": [],
            },
            category="code",
        ),
        CoreGitLogTool(workspace_root=workspace_dir),
    )
    registry.register(
        ToolSpec(
            name="find_symbol",
            description="Find class, function, or variable definitions in Python files using AST.",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "path": {"type": "string"},
                    "kind": {"type": "string", "enum": ["any", "class", "function", "variable"]},
                    "max_results": {"type": "integer"},
                },
                "required": ["symbol"],
            },
            side_effect="local_fs",
            category="code",
            sandbox_required=True,
        ),
        CoreFindSymbolTool(workspace_root=workspace_dir),
    )

    event_bus = EventBus()
    if enable_event_logging:
        event_bus.subscribe(LoggingSubscriber())
    event_bus.subscribe(AuditSubscriber())
    guardrail_engine = GuardrailEngine(workspace_root=workspace_dir, skill_roots=skill_roots)
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
    # Build a shared SkillTool instance so MessageBuilder can inject skill listings
    skill_tool_instance = CoreSkillTool(workspace_root=workspace_dir, skill_roots=skill_roots)
    runtime = AgentRuntime(
        llm_client=NativeToolCallingLLMClient(llm=llm, model_name=active_model_name),
        message_builder=MessageBuilder(
            short_memory_turns=runtime_config.short_memory_turns,
            skill_tool=skill_tool_instance,
            workspace_root=workspace_dir,
        ),
        tool_executor=tool_executor,
        registry=registry,
        session_store=FileSessionStore(SESSION_DIR),
        checkpoint_store=FileCheckpointStore(CHECKPOINT_DIR),
        event_bus=event_bus,
        guardrail_engine=guardrail_engine,
        budget_controller=BudgetController(
            max_steps=runtime_config.max_steps,
            max_tool_calls=max_tool_calls if max_tool_calls is not None else runtime_config.max_tool_calls,
            max_seconds=runtime_config.max_seconds,
            max_state_tool_calls=max_state_tool_calls if max_state_tool_calls is not None else runtime_config.max_state_tool_calls,
            max_read_tool_calls=max_read_tool_calls if max_read_tool_calls is not None else runtime_config.max_read_tool_calls,
            max_network_tool_calls=max_network_tool_calls if max_network_tool_calls is not None else runtime_config.max_network_tool_calls,
            max_high_risk_tool_calls=max_high_risk_tool_calls if max_high_risk_tool_calls is not None else runtime_config.max_high_risk_tool_calls,
        ),
        idempotency_store=idempotency_store,
    )
    runtime.memory_manager = MemoryManager(
        store=FileMemoryStore(MEMORY_USERS_DIR),
        workspace_id=str(workspace_dir),
    )
    runtime.compaction_pipeline = CompactionPipeline(
        summarizer=TranscriptSummarizer(llm=llm),
        archive_dir=MEMORY_TRANSCRIPTS_DIR,
    )
    return runtime


def build_llm(config_path: str | Path | None = None) -> BaseChatModel:
    """仅创建并返回 LLM 实例，供不需要完整 runtime 的场景使用（如 Planner）。"""
    from langchain_core.language_models import BaseChatModel  # noqa: F401 (type hint only)
    runtime_config = load_runtime_config(config_path)
    loader = create_model_loader(
        model_url=runtime_config.model_url,
        model_name=runtime_config.model_name,
        api_key=runtime_config.api_key,
        temperature=runtime_config.temperature,
    )
    return loader.load()
