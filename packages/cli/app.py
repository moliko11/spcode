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
    WorkspaceOpt,
)
from .repl_input import create_prompt_session, read_repl_input

console = Console()


def _split_repl_command(raw: str) -> tuple[str, str]:
    """把 /command arg 拆成命令和参数，便于测试和复用。"""
    text = raw[1:] if raw.startswith("/") else raw
    cmd, _, arg = text.strip().partition(" ")
    return cmd.lower(), arg.strip()


def _configure_utf8_stdio() -> None:
    """Prefer UTF-8 for redirected CLI logs on Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

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
    workspace: WorkspaceOpt = None,
    json_output: JsonOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Personal Code Agent — 不带子命令时进入交互 REPL。"""
    _configure_utf8_stdio()
    ctx.ensure_object(dict)
    ctx.obj = GlobalOptions(
        provider=provider,
        user_id=user_id,
        session_id=session_id,
        workspace=workspace or os.getcwd(),
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
    终端工作台 REPL。

    默认输入走流式 chat；斜杠命令提供计划、执行、审批和查询入口。
    """
    from .commands.chat_stream import _stream_once
    from .render import render_workbench_home

    render_workbench_home(opts, _snapshot_workbench())
    prompt_session = create_prompt_session()

    while True:
        try:
            raw = read_repl_input(console, prompt_session).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/]")
            break

        if not raw:
            continue

        # 斜杠命令
        if raw.startswith("/"):
            _handle_slash(raw, opts)
            continue

        # 普通消息 → 流式 chat
        asyncio.run(_stream_once(opts, raw))


def _handle_slash(raw: str, opts: GlobalOptions) -> None:
    """处理 REPL 内斜杠命令。"""
    cmd, arg = _split_repl_command(raw)

    if cmd in ("exit", "quit", "q"):
        console.print("[dim]bye[/]")
        raise SystemExit(0)
    elif cmd == "help":
        from .render import render_workbench_help
        render_workbench_help()
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
    elif cmd in ("history", "hist"):
        _show_history(opts, arg)
    elif cmd in ("memory", "mem"):
        from .commands.memory import _list_memories
        asyncio.run(_list_memories(opts))
    elif cmd == "plans":
        from packages.app_service.query_service import QueryService
        from .render import render_plans_table
        render_plans_table(QueryService.from_env().list_plans(limit=20))
    elif cmd == "runs":
        from .commands.runs import _list_runs
        _list_runs(opts)
    elif cmd in ("approvals", "approval"):
        from packages.app_service.query_service import QueryService
        from .render import render_plan_runs_table
        waiting = QueryService.from_env().list_plan_runs(status_filter="waiting_human", limit=50)
        render_plan_runs_table(waiting)
    elif cmd in ("plan", "p"):
        if not arg:
            console.print("[dim]用法: /plan <goal>[/]")
            return
        from .commands.plans import _create_plan
        asyncio.run(_create_plan(arg, "", opts.provider, opts.json_output))
    elif cmd in ("run", "orchestrate"):
        if not arg:
            console.print("[dim]用法: /run <goal>[/]")
            return
        from .commands.plans import _run_plan
        asyncio.run(_run_plan(arg, "", opts.provider, opts.user_id, opts.workspace, opts.json_output))
    elif cmd == "approve":
        if not arg:
            console.print("[dim]用法: /approve <plan_run_id>[/]")
            return
        from .commands.approvals import _approve
        asyncio.run(_approve(arg, None, opts.provider, opts.user_id, opts.workspace, opts.json_output))
    elif cmd == "reject":
        if not arg:
            console.print("[dim]用法: /reject <plan_run_id>[/]")
            return
        from .commands.approvals import _reject
        asyncio.run(_reject(arg, "rejected from workbench", opts.provider, opts.user_id, opts.workspace))
    elif cmd == "recover":
        if not arg:
            console.print("[dim]用法: /recover <plan_run_id>[/]")
            return
        from .commands.approvals import _recover
        asyncio.run(_recover(arg, opts.provider, opts.user_id, opts.workspace, opts.json_output))
    elif cmd == "status":
        from .render import render_workbench_status
        render_workbench_status(opts, _snapshot_workbench())
    else:
        console.print(f"[dim]未知命令 /{cmd}，输入 /help 查看可用命令[/]")


def _parse_history_limit(arg: str, default: int = 30) -> int:
    if not arg:
        return default
    try:
        value = int(arg.strip())
    except ValueError:
        return default
    return max(1, value)


def _show_history(opts: GlobalOptions, arg: str = "") -> None:
    from packages.app_service.query_service import QueryService
    from .render import render_session_history

    limit = _parse_history_limit(arg)
    messages = asyncio.run(QueryService.from_env().get_session_messages(opts.session_id))
    render_session_history(opts.session_id, messages, limit=limit)


def _snapshot_workbench() -> dict[str, int]:
    """读取工作台概览；失败时返回空计数，避免 REPL 启动被查询错误打断。"""
    try:
        from packages.app_service.query_service import QueryService
        qs = QueryService.from_env()
        return {
            "plans": len(qs.list_plans(limit=200)),
            "runs": len(qs.list_plan_runs(limit=200)),
            "waiting": len(qs.list_plan_runs(status_filter="waiting_human", limit=200)),
        }
    except Exception:
        return {}


# ── 子命令注册 ─────────────────────────────────────────────────────────────
from .commands import chat, chat_stream, runs, plans, approvals, memory, serve  # noqa: E402

app.add_typer(chat.app,      name="chat",      help="单轮/多轮 chat")
app.add_typer(chat_stream.app, name="chat-stream", help="终端流式 chat（新接口）")
app.add_typer(runs.app,      name="runs",      help="查看 / 监控 run")
app.add_typer(plans.app,     name="plans",     help="生成 / 查看计划")
app.add_typer(approvals.app, name="approvals", help="处理待审批操作")
app.add_typer(memory.app,    name="memory",    help="查看 / 管理记忆")
app.add_typer(serve.app,     name="serve",     help="启动 API server")


if __name__ == "__main__":
    app()
