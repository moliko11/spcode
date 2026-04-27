"""
commands/memory.py — 记忆管理

  agent memory list [--user-id demo-user] [--type semantic]
  agent memory show <memory_id>
  agent memory forget <memory_id>
  agent sessions list
  agent sessions show <session_id>
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Optional

import typer

from packages.cli.options import GlobalOptions, JsonOpt, UserIdOpt
from packages.cli.render import (
    console,
    print_error,
    print_json,
    print_success,
    render_memories_table,
)

app = typer.Typer(help="查看 / 管理 Agent 记忆")


@app.command("list")
def list_memories_cmd(
    user_id: UserIdOpt = "demo-user",
    memory_type: Annotated[Optional[str], typer.Option("--type", help="episode|semantic|procedural")] = None,
    limit: Annotated[int, typer.Option("--limit", help="最多显示条数")] = 20,
    json_output: JsonOpt = False,
) -> None:
    """列出指定用户的记忆。"""
    opts = GlobalOptions(user_id=user_id, json_output=json_output)
    asyncio.run(_list_memories(opts, memory_type=memory_type, limit=limit))


async def _list_memories(
    opts: GlobalOptions,
    memory_type: str | None = None,
    limit: int = 20,
) -> None:
    from packages.app_service.query_service import QueryService

    qs = QueryService.from_env()
    memories = await qs.list_memories(
        user_id=opts.user_id,
        limit=limit,
        memory_type=memory_type,
    )

    if opts.json_output:
        print_json(memories)
    else:
        render_memories_table(memories)
        console.print(f"\n[dim]共 {len(memories)} 条记忆 (user_id={opts.user_id})[/]")


@app.command("show")
def show_memory_cmd(
    memory_id: Annotated[str, typer.Argument(help="memory_id")],
    user_id: UserIdOpt = "demo-user",
    json_output: JsonOpt = False,
) -> None:
    """显示单条记忆的完整内容。"""
    asyncio.run(_show_memory(memory_id, user_id, json_output))


async def _show_memory(memory_id: str, user_id: str, json_output: bool) -> None:
    from packages.app_service.query_service import QueryService
    from rich.panel import Panel

    qs = QueryService.from_env()
    memories = await qs.list_memories(user_id=user_id, limit=200)
    entry = next((m for m in memories if m.get("id", "").startswith(memory_id)), None)

    if entry is None:
        print_error(f"memory {memory_id!r} not found")
        raise typer.Exit(1)

    if json_output:
        print_json(entry)
    else:
        mem_type = entry.get("memory_type", "")
        type_color = {"episode": "cyan", "semantic": "magenta", "procedural": "yellow"}.get(mem_type, "white")
        console.print(Panel(
            entry.get("content", ""),
            title=f"[{type_color}]{mem_type}[/]  id={entry.get('id', '')}",
            border_style="dim",
        ))
        tags = entry.get("tags", [])
        if tags:
            console.print(f"  tags: {', '.join(tags)}")


@app.command("forget")
def forget_cmd(
    memory_id: Annotated[str, typer.Argument(help="memory_id")],
    user_id: UserIdOpt = "demo-user",
    yes: Annotated[bool, typer.Option("--yes", "-y", is_flag=True, help="跳过确认")] = False,
) -> None:
    """删除一条记忆。"""
    if not yes:
        confirmed = typer.confirm(f"确认删除记忆 {memory_id}?")
        if not confirmed:
            raise typer.Abort()
    asyncio.run(_forget_memory(memory_id, user_id))


async def _forget_memory(memory_id: str, user_id: str) -> None:
    from packages.memory.store import FileMemoryStore
    from packages.runtime.config import MEMORY_USERS_DIR

    store = FileMemoryStore(MEMORY_USERS_DIR)
    await store.delete(memory_id)
    print_success(f"记忆 {memory_id} 已删除")


# ── sessions ─────────────────────────────────────────────────────────────


@app.command("sessions")
def sessions_cmd(
    action: Annotated[str, typer.Argument(help="list | show")] = "list",
    session_id: Annotated[Optional[str], typer.Argument(help="session_id (show 时使用)")] = None,
    user_id: UserIdOpt = "demo-user",
    json_output: JsonOpt = False,
) -> None:
    """
    查看会话历史。

      agent memory sessions list
      agent memory sessions show <session_id>
    """
    asyncio.run(_sessions(action, session_id, user_id, json_output))


async def _sessions(
    action: str,
    session_id: str | None,
    user_id: str,
    json_output: bool,
) -> None:
    from packages.app_service.query_service import QueryService

    qs = QueryService.from_env()

    if action == "list":
        sessions = qs.list_sessions(limit=20)
        if json_output:
            print_json(sessions)
        else:
            if not sessions:
                console.print("[dim]No sessions found.[/]")
                return
            from rich.table import Table
            from rich import box
            t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
            t.add_column("session_id")
            t.add_column("updated_at")
            for s in sessions:
                t.add_row(s.get("session_id", ""), str(s.get("updated_at", "")))
            console.print(t)

    elif action == "show":
        if not session_id:
            print_error("show 需要提供 session_id")
            raise typer.Exit(1)
        messages = await qs.get_session_messages(session_id)
        if json_output:
            print_json(messages)
        else:
            for msg in messages:
                role = msg.get("role", "")
                color = "green" if role == "user" else "blue"
                console.print(f"[bold {color}]{role}[/]: {msg.get('content', '')[:200]}")
    else:
        print_error(f"未知操作: {action}，可用: list | show")
        raise typer.Exit(1)
