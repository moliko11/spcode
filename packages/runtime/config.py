from __future__ import annotations

import datetime
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

MODEL_URL = "http://10.8.160.47:9998/v1"
MODEL_NAME = "qwen3"
API_KEY = "EMPTY"
TEMPERATURE = 0.5

# Project root: packages/runtime/ -> packages/ -> project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "agent.config.yaml"
# Skills root: configurable via AGENT_SKILLS_DIR env var; defaults to PROJECT_ROOT/skills/
# In production, point this to a directory outside the source tree to avoid committing secrets.
_skills_env = os.getenv("AGENT_SKILLS_DIR")
SKILL_ROOTS = [Path(_skills_env).resolve() if _skills_env else PROJECT_ROOT / "skills"]

RUNTIME_DIR = Path("./runtime_data")
SESSION_DIR = RUNTIME_DIR / "sessions"
CHECKPOINT_DIR = RUNTIME_DIR / "checkpoints"
WORKSPACE_DIR = Path(os.getenv("AGENT_WORKSPACE", "./runtime_data/workspace"))
AUDIT_LOG_PATH = RUNTIME_DIR / "audit.log"
MODEL_INPUT_AUDIT_LOG_PATH = Path(os.getenv("AGENT_MODEL_INPUT_AUDIT_LOG", RUNTIME_DIR / "model_input_audit.jsonl"))
MODEL_INPUT_AUDIT_ENABLED = os.getenv("AGENT_MODEL_INPUT_AUDIT", "1").lower() in {"1", "true", "yes", "on"}
MODEL_INPUT_AUDIT_INCLUDE_CONTENT = os.getenv("AGENT_MODEL_INPUT_AUDIT_CONTENT", "1").lower() in {"1", "true", "yes", "on"}

MEMORY_DIR = RUNTIME_DIR / "memory"
MEMORY_USERS_DIR = MEMORY_DIR / "users"
MEMORY_TRANSCRIPTS_DIR = MEMORY_DIR / "transcripts"
MEMORY_COMPACTION_DIR = MEMORY_DIR / "compaction"

PLANS_DIR = RUNTIME_DIR / "plans"
PLAN_RUNS_DIR = RUNTIME_DIR / "plan_runs"
WORKFLOWS_DIR = RUNTIME_DIR / "workflows"

MAX_STEPS = 20
MAX_TOOL_CALLS = 20
MAX_STATE_TOOL_CALLS = 120
MAX_READ_TOOL_CALLS = 120
MAX_NETWORK_TOOL_CALLS = 30
MAX_HIGH_RISK_TOOL_CALLS = 20
MAX_SECONDS = 160
SHORT_MEMORY_TURNS = 8

DEFAULT_READ_MAX_BYTES = 64 * 1024
DEFAULT_SHELL_OUTPUT_LIMIT = 20_000
# CURRENT_DATE: defaults to today's real date; override via env for testing
CURRENT_DATE = os.getenv("CURRENT_DATE", datetime.date.today().isoformat())
CURRENT_TIMEZONE = os.getenv("CURRENT_TIMEZONE", "Asia/Shanghai")

DEFAULT_LOADED_TOOL_NAMES = [
    "get_current_time",
    "calculator",
    "enter_plan_mode",
    "exit_plan_mode",
    "file_read",
    "file_write",
    "file_edit",
    "glob",
    "grep",
    "list_dir",
    "web_search",
    "web_fetch",
    "bash",
    "tool_search",
    "todo_write",
    "skill",
    "run_tests",
    "lint",
    "git_diff",
    "git_log",
    "find_symbol",
]

DYNAMIC_TOOL_NAMES = ["task_create", "task_update", "task_list", "task_output", "task_verify", "task_replan", "task_stop", "mcp"]

TOOL_CATALOG = [
    {"name": "get_current_time", "description": "Read the current local time in the runtime environment.", "category": "utility", "tags": ["time", "clock", "date"], "default_loaded": True, "requires_approval": False},
    {"name": "calculator", "description": "Evaluate small arithmetic expressions.", "category": "utility", "tags": ["math", "calculate", "expression"], "default_loaded": True, "requires_approval": False},
    {"name": "enter_plan_mode", "description": "Enter side-effect-free plan mode for analysis and planning.", "category": "workflow", "tags": ["plan", "mode", "workflow", "safety"], "default_loaded": True, "requires_approval": False},
    {"name": "exit_plan_mode", "description": "Exit plan mode with an approved, rejected, or revise_required decision.", "category": "workflow", "tags": ["plan", "mode", "workflow", "approval"], "default_loaded": True, "requires_approval": False},
    {"name": "file_read", "description": "Read files or line ranges from the local workspace.", "category": "workspace", "tags": ["file", "read", "workspace", "inspect"], "default_loaded": True, "requires_approval": False},
    {"name": "file_write", "description": "Create, overwrite, or append files in the local workspace.", "category": "workspace", "tags": ["file", "write", "create", "append"], "default_loaded": True, "requires_approval": True},
    {"name": "file_edit", "description": "Apply precise edits to existing workspace files.", "category": "workspace", "tags": ["file", "edit", "replace", "insert"], "default_loaded": True, "requires_approval": True},
    {"name": "glob", "description": "Find files by glob pattern inside the workspace.", "category": "workspace", "tags": ["glob", "files", "discover", "paths"], "default_loaded": True, "requires_approval": False},
    {"name": "grep", "description": "Search text or code content inside the workspace.", "category": "workspace", "tags": ["grep", "search", "code", "text"], "default_loaded": True, "requires_approval": False},
    {"name": "list_dir", "description": "List files and directories in the workspace.", "category": "workspace", "tags": ["list", "directory", "files", "workspace"], "default_loaded": True, "requires_approval": False},
    {"name": "web_search", "description": "Search the web for current external information.", "category": "web", "tags": ["web", "search", "internet", "research"], "default_loaded": True, "requires_approval": False},
    {"name": "web_fetch", "description": "Fetch and inspect specific URLs.", "category": "web", "tags": ["web", "fetch", "url", "page"], "default_loaded": True, "requires_approval": False},
    {"name": "bash", "description": "Run shell commands in the workspace when specialized tools are insufficient.", "category": "execution", "tags": ["bash", "shell", "command", "terminal"], "default_loaded": True, "requires_approval": True},
    {"name": "tool_search", "description": "Discover available tools and recommend which ones to load next.", "category": "meta", "tags": ["tools", "discover", "catalog", "load"], "default_loaded": True, "requires_approval": False},
    {"name": "todo_write", "description": "Create or update a lightweight visible todo list backed by WorkflowStore.", "category": "workflow", "tags": ["todo", "task", "workflow", "plan", "status"], "default_loaded": True, "requires_approval": False},
    {"name": "task_create", "description": "Create persisted workflow tasks inside workflows.", "category": "workflow", "tags": ["task", "workflow", "plan", "create"], "default_loaded": False, "requires_approval": False},
    {"name": "task_update", "description": "Update persisted workflow task status, output, evidence, and metadata.", "category": "workflow", "tags": ["task", "workflow", "status", "update"], "default_loaded": False, "requires_approval": False},
    {"name": "task_list", "description": "List persisted workflow tasks from recent plans, a specific plan, or a plan run.", "category": "workflow", "tags": ["task", "workflow", "list", "progress"], "default_loaded": False, "requires_approval": False},
    {"name": "task_output", "description": "Read workflow task, plan, or plan run output.", "category": "workflow", "tags": ["task", "workflow", "output", "evidence"], "default_loaded": False, "requires_approval": False},
    {"name": "task_verify", "description": "Verify a workflow task using acceptance criteria and optional test-command evidence.", "category": "workflow", "tags": ["task", "workflow", "verify", "test", "evidence"], "default_loaded": False, "requires_approval": False},
    {"name": "task_replan", "description": "Append or replace follow-up workflow tasks after a failed task.", "category": "workflow", "tags": ["task", "workflow", "replan", "failure", "retry"], "default_loaded": False, "requires_approval": False},
    {"name": "task_stop", "description": "Stop a task, plan, or plan run.", "category": "workflow", "tags": ["task", "workflow", "stop", "cancel"], "default_loaded": False, "requires_approval": False},
    {"name": "skill", "description": "Discover, inspect, and read local skills.", "category": "meta", "tags": ["skill", "workflow", "prompt", "local"], "default_loaded": False, "requires_approval": False},
    {"name": "mcp", "description": "Inspect locally configured MCP servers, resources, prompts, and tools.", "category": "integration", "tags": ["mcp", "server", "resource", "integration"], "default_loaded": False, "requires_approval": False},
    {"name": "run_tests", "description": "Run the project test suite (pytest) and return pass/fail results.", "category": "code", "tags": ["test", "pytest", "quality", "ci"], "default_loaded": True, "requires_approval": False},
    {"name": "lint", "description": "Run ruff (or pyflakes) on workspace code and return lint issues.", "category": "code", "tags": ["lint", "ruff", "quality", "static-analysis"], "default_loaded": True, "requires_approval": False},
    {"name": "git_diff", "description": "Show git diff for workspace changes, staged files, or between commits.", "category": "code", "tags": ["git", "diff", "changes", "review"], "default_loaded": True, "requires_approval": False},
    {"name": "git_log", "description": "Show git commit history to understand code evolution.", "category": "code", "tags": ["git", "log", "history", "commits"], "default_loaded": True, "requires_approval": False},
    {"name": "find_symbol", "description": "Find class, function, or variable definitions in Python files using AST.", "category": "code", "tags": ["ast", "symbol", "navigate", "definition"], "default_loaded": True, "requires_approval": False},
]


@dataclass(slots=True)
class RuntimeConfig:
    model_url: str
    model_name: str
    api_key: str
    temperature: float
    workspace_root: Path
    skill_roots: list[Path]
    short_memory_turns: int
    max_steps: int
    max_tool_calls: int
    max_state_tool_calls: int
    max_read_tool_calls: int
    max_network_tool_calls: int
    max_high_risk_tool_calls: int
    max_seconds: int
    source_path: Path | None = None


def resolve_runtime_config_path(config_path: str | Path | None = None) -> Path:
    env_path = os.getenv("AGENT_CONFIG")
    candidate = config_path or env_path or DEFAULT_CONFIG_PATH
    return Path(candidate).resolve()


def load_runtime_config(config_path: str | Path | None = None) -> RuntimeConfig:
    path = resolve_runtime_config_path(config_path)
    raw: dict[str, object] = {}
    source_path: Path | None = None
    if path.exists():
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError("agent config must be a mapping at the top level")
        raw = payload
        source_path = path
    base_dir = source_path.parent if source_path is not None else PROJECT_ROOT

    model_section = _mapping(raw.get("model"), label="model")
    runtime_section = _mapping(raw.get("runtime"), label="runtime")
    budget_section = _mapping(raw.get("budget"), label="budget")
    skills_section = _mapping(raw.get("skills"), label="skills")

    workspace_value = runtime_section.get("workspace_root", WORKSPACE_DIR)
    skill_roots_value = skills_section.get("roots", SKILL_ROOTS)

    return RuntimeConfig(
        model_url=_as_str(model_section.get("url", MODEL_URL), label="model.url"),
        model_name=_as_str(model_section.get("name", MODEL_NAME), label="model.name"),
        api_key=_as_str(model_section.get("api_key", API_KEY), label="model.api_key"),
        temperature=_as_float(model_section.get("temperature", TEMPERATURE), label="model.temperature"),
        workspace_root=_as_path(workspace_value, base_dir=base_dir),
        skill_roots=_as_path_list(skill_roots_value, label="skills.roots", base_dir=base_dir),
        short_memory_turns=_as_int(runtime_section.get("short_memory_turns", SHORT_MEMORY_TURNS), label="runtime.short_memory_turns"),
        max_steps=_as_int(budget_section.get("max_steps", MAX_STEPS), label="budget.max_steps"),
        max_tool_calls=_as_int(budget_section.get("max_tool_calls", MAX_TOOL_CALLS), label="budget.max_tool_calls"),
        max_state_tool_calls=_as_int(budget_section.get("max_state_tool_calls", MAX_STATE_TOOL_CALLS), label="budget.max_state_tool_calls"),
        max_read_tool_calls=_as_int(budget_section.get("max_read_tool_calls", MAX_READ_TOOL_CALLS), label="budget.max_read_tool_calls"),
        max_network_tool_calls=_as_int(budget_section.get("max_network_tool_calls", MAX_NETWORK_TOOL_CALLS), label="budget.max_network_tool_calls"),
        max_high_risk_tool_calls=_as_int(budget_section.get("max_high_risk_tool_calls", MAX_HIGH_RISK_TOOL_CALLS), label="budget.max_high_risk_tool_calls"),
        max_seconds=_as_int(budget_section.get("max_seconds", MAX_SECONDS), label="budget.max_seconds"),
        source_path=source_path,
    )


def _mapping(value: object, *, label: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _as_str(value: object, *, label: str) -> str:
    if isinstance(value, str):
        return value
    raise ValueError(f"{label} must be a string")


def _as_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _as_float(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    return float(value)


def _as_path(value: object, *, base_dir: Path) -> Path:
    if isinstance(value, Path):
        return value.resolve() if value.is_absolute() else (base_dir / value).resolve()
    if isinstance(value, str):
        candidate = Path(os.path.expanduser(os.path.expandvars(value)))
        return candidate.resolve() if candidate.is_absolute() else (base_dir / candidate).resolve()
    raise ValueError("path values must be strings")


def _as_path_list(value: object, *, label: str, base_dir: Path) -> list[Path]:
    if isinstance(value, (str, Path)):
        return [_as_path(value, base_dir=base_dir)]
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of paths")
    return [_as_path(item, base_dir=base_dir) for item in value]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("agent_runtime")
