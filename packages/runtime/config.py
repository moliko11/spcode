from __future__ import annotations

import logging
import os
from pathlib import Path

MODEL_URL = "http://10.8.160.47:9998/v1"
MODEL_NAME = "qwen3"
API_KEY = "EMPTY"
TEMPERATURE = 0.5

RUNTIME_DIR = Path("./runtime_data")
SESSION_DIR = RUNTIME_DIR / "sessions"
CHECKPOINT_DIR = RUNTIME_DIR / "checkpoints"
WORKSPACE_DIR = Path(os.getenv("AGENT_WORKSPACE", "./runtime_data/workspace"))
AUDIT_LOG_PATH = RUNTIME_DIR / "audit.log"

MEMORY_DIR = RUNTIME_DIR / "memory"
MEMORY_USERS_DIR = MEMORY_DIR / "users"
MEMORY_TRANSCRIPTS_DIR = MEMORY_DIR / "transcripts"
MEMORY_COMPACTION_DIR = MEMORY_DIR / "compaction"

MAX_STEPS = 10
MAX_TOOL_CALLS = 6
MAX_SECONDS = 160
SHORT_MEMORY_TURNS = 8

DEFAULT_READ_MAX_BYTES = 64 * 1024
DEFAULT_SHELL_OUTPUT_LIMIT = 20_000
# CURRENT_DATE: 用于模拟当前日期，用于测试和调试
CURRENT_DATE = os.getenv("CURRENT_DATE", "2026-04-17")
CURRENT_TIMEZONE = os.getenv("CURRENT_TIMEZONE", "Asia/Shanghai")

DEFAULT_LOADED_TOOL_NAMES = [
    "get_current_time",
    "calculator",
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
]

DYNAMIC_TOOL_NAMES = ["skill", "mcp"]

TOOL_CATALOG = [
    {"name": "get_current_time", "description": "Read the current local time in the runtime environment.", "category": "utility", "tags": ["time", "clock", "date"], "default_loaded": True, "requires_approval": False},
    {"name": "calculator", "description": "Evaluate small arithmetic expressions.", "category": "utility", "tags": ["math", "calculate", "expression"], "default_loaded": True, "requires_approval": False},
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
    {"name": "skill", "description": "Discover, inspect, and read local skills.", "category": "meta", "tags": ["skill", "workflow", "prompt", "local"], "default_loaded": False, "requires_approval": False},
    {"name": "mcp", "description": "Inspect locally configured MCP servers, resources, prompts, and tools.", "category": "integration", "tags": ["mcp", "server", "resource", "integration"], "default_loaded": False, "requires_approval": False},
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("agent_runtime")
