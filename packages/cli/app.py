"""
app.py — Typer CLI 入口

命令树：
  agent                     （无参数：进入 REPL 模式）
  agent chat "message"
  agent plan "goal"
  agent run "goal"
  agent runs list/show/watch
  agent plan-runs list/show
  agent approve <plan_run_id>
  agent reject  <plan_run_id>
  agent recover <plan_run_id>
  agent memory show/list
  agent sessions show <session_id>
  agent serve api

pyproject.toml [project.scripts] 中配置：
  agent = "packages.cli.app:app"
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Annotated, Optional

import typer
from rich.console import Console

from .options import (
    GlobalOptions,
    JsonOpt,
    ProviderOpt,
    SessionIdOpt,
    UserIdOpt,
    VerboseOpt,
)

console = Console()

# ── 根 app ────────────────────────────────────────────────────────────────
app = typer.Typer(
    name="agent",
    help="Personal Code Agent CLI — chat / plan / orchestrate / approve",
    no_args_is_help=False,   # 无参数时走 callback
    invoke_without_command=True,
    add_completion=True,
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    provider: ProviderOpt = "openai_compatible",
    user_id: UserIdOpt = "demo-user",
    session_id: SessionIdOpt = "demo-session",
    json_output: JsonOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Personal Code Agent — 不带子命令时进入交互 REPL。"""
    ctx.ensure_object(dict)
    ctx.obj = GlobalOptions(
        provider=provider,
        user_id=user_id,
        session_id=session_id,
        json_output=json_output,
        verbose=verbose,
    )

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    if ctx.invoked_subcommand is None:
        # 无子命令 → 简易 REPL（后续可替换为 Textual TUI）
        _run_repl(ctx.obj)


def _run_repl(opts: GlobalOptions) -> None:
    """
    简易交互 REPL（占位）。
    后续用 Textual TUI 替换，目前保持 chat 体验可用。
    """
    from .commands.chat import _do_chat

    console.print("[bold green]Agent REPL[/]  (type [bold]/help[/] for commands, [bold]Ctrl+D[/] to exit)\n")
    console.print(
        f"  provider=[cyan]{opts.provider}[/]  user=[cyan]{opts.user_id}[/]  session=[cyan]{opts.session_id}[/]\n"
    )

    while True:
        try:
            raw = console.input("[bold green]>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/]")
            break

        if not raw:
            continue

        # 斜杠命令
        if raw.startswith("/"):
            _handle_slash(raw, opts)
            continue

        # 普通消息 → chat
        asyncio.run(_do_chat(opts, raw))


def _handle_slash(raw: str, opts: GlobalOptions) -> None:
    """处理 REPL 内斜杠命令（简化版，后续扩展）。"""
    parts = raw[1:].split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("exit", "quit", "q"):
        console.print("[dim]bye[/]")
        raise SystemExit(0)
    elif cmd == "help":
        console.print(
            "  /clear     — 清除会话历史\n"
            "  /cost      — 显示 token 统计\n"
            "  /memory    — 查看记忆\n"
            "  /model <p> — 切换 provider\n"
            "  /plan      — 进入计划模式\n"
            "  /runs      — 列出最近 run\n"
            "  /status    — 当前状态\n"
            "  /quit      — 退出\n"
        )
    elif cmd == "clear":
        import uuid
        opts.session_id = f"session-{uuid.uuid4().hex[:8]}"
        console.print(f"[dim]新会话 session_id={opts.session_id}[/]")
    elif cmd == "model":
        if arg:
            opts.provider = arg
            os.environ["MOLIKO_LLM_PROVIDER"] = arg
            console.print(f"[dim]切换到 provider={arg}[/]")
        else:
            console.print(f"[dim]当前 provider={opts.provider}[/]")
    elif cmd == "cost":
        console.print("[dim]/cost 需要运行中的 run，请先发送一条消息[/]")
    elif cmd in ("memory", "mem"):
        from .commands.memory import _list_memories
        asyncio.run(_list_memories(opts))
    elif cmd == "runs":
        from .commands.runs import _list_runs
        _list_runs(opts)
    elif cmd == "status":
        console.print(f"  provider=[cyan]{opts.provider}[/]  user=[cyan]{opts.user_id}[/]  session=[cyan]{opts.session_id}[/]")
    else:
        console.print(f"[dim]未知命令 /{cmd}，输入 /help 查看可用命令[/]")


# ── 子命令注册 ─────────────────────────────────────────────────────────────
from .commands import chat, runs, plans, approvals, memory, serve  # noqa: E402

app.add_typer(chat.app,      name="chat",      help="单轮/多轮 chat")
app.add_typer(runs.app,      name="runs",      help="查看 / 监控 run")
app.add_typer(plans.app,     name="plans",     help="生成 / 查看计划")
app.add_typer(approvals.app, name="approvals", help="处理待审批操作")
app.add_typer(memory.app,    name="memory",    help="查看 / 管理记忆")
app.add_typer(serve.app,     name="serve",     help="启动 API server")


if __name__ == "__main__":
    app()
