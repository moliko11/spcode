"""
options.py — CLI 全局选项

所有子命令共用的 typer 选项和上下文对象。
通过 typer callback 注入到 ctx.obj。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

import typer


@dataclass
class GlobalOptions:
    provider: str = "openai_compatible"
    user_id: str = "demo-user"
    session_id: str = "demo-session"
    json_output: bool = False
    verbose: bool = False
    max_tool_calls: int | None = None
    max_state_tool_calls: int | None = None
    max_read_tool_calls: int | None = None
    max_network_tool_calls: int | None = None
    max_high_risk_tool_calls: int | None = None


# ── 常用 Annotated 类型 ───────────────────────────────────────────────────

ProviderOpt = Annotated[
    str,
    typer.Option("--provider", "-p", help="LLM provider (openai_compatible | mock)", envvar="AGENT_PROVIDER"),
]

UserIdOpt = Annotated[
    str,
    typer.Option("--user-id", "-u", help="User ID", envvar="AGENT_USER_ID"),
]

SessionIdOpt = Annotated[
    str,
    typer.Option("--session-id", "-s", help="Session ID", envvar="AGENT_SESSION_ID"),
]

JsonOpt = Annotated[
    bool,
    typer.Option("--json", is_flag=True, help="Output as JSON (for scripting)"),
]

VerboseOpt = Annotated[
    bool,
    typer.Option("--verbose", "-v", is_flag=True, help="Show debug info"),
]
