"""
commands/chat_stream.py — 终端流式 chat 子命令

  agent chat-stream "message"
  agent chat-stream              # 进入流式多轮模式
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Optional

import typer

from packages.cli.options import GlobalOptions, JsonOpt, ProviderOpt, SessionIdOpt, UserIdOpt
from packages.cli.render import (
    build_stream_event_view,
    console,
    print_error,
    render_stream_event_view,
    render_approval_prompt,
    render_approval_request,
)
from packages.runtime.models import AgentEvent


app = typer.Typer(help="终端流式 chat（新接口）")


@app.callback(invoke_without_command=True)
def chat_stream_cmd(
    ctx: typer.Context,
    message: Annotated[Optional[str], typer.Argument(help="发送给 agent 的消息")] = None,
    provider: ProviderOpt = "openai_compatible",
    user_id: UserIdOpt = "demo-user",
    session_id: SessionIdOpt = "demo-session",
    json_output: JsonOpt = False,
) -> None:
    root_opts = ctx.find_root().obj if isinstance(ctx.find_root().obj, GlobalOptions) else GlobalOptions()
    opts = GlobalOptions(
        provider=root_opts.provider,
        user_id=root_opts.user_id,
        session_id=root_opts.session_id,
        json_output=root_opts.json_output,
        verbose=root_opts.verbose,
    )
    if provider != "openai_compatible":
        opts.provider = provider
    if user_id != "demo-user":
        opts.user_id = user_id
    if session_id != "demo-session":
        opts.session_id = session_id
    if json_output:
        opts.json_output = True

    if message is None:
        _run_stream_repl(opts)
        return
    asyncio.run(_stream_once(opts, message))


def _run_stream_repl(opts: GlobalOptions) -> None:
    console.print("[bold green]Stream Chat Mode[/]  (type [bold]exit[/] to stop)\n")
    console.print(
        f"  provider=[cyan]{opts.provider}[/]  user=[cyan]{opts.user_id}[/]  session=[cyan]{opts.session_id}[/]\n"
    )
    while True:
        try:
            raw = console.input("[bold green]you>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/]")
            break
        if not raw:
            continue
        if raw.lower() in {"exit", "quit"}:
            console.print("[dim]bye[/]")
            break
        asyncio.run(_stream_once(opts, raw))


async def _stream_once(opts: GlobalOptions, message: str) -> None:
    from packages.app_service.chat_service import ChatService, HumanDecision

    streamed_any = False
    line_open = False
    latest_cost: dict[str, object] = {}

    async def _on_event(event: AgentEvent) -> None:
        nonlocal streamed_any, line_open, latest_cost
        view = build_stream_event_view(event)
        if view.category == "token":
            if view.text:
                if not line_open:
                    console.print("assistant> ", end="")
                    line_open = True
                console.print(view.text, end="", soft_wrap=True)
                streamed_any = True
            return

        if view.category == "ignore":
            return

        if view.category == "usage":
            latest_cost = view.payload

        if view.category == "approval":
            if line_open:
                console.print()
                line_open = False
            render_approval_request({"context": view.payload})
            return

        if view.category in {"thinking", "usage", "generic"} or (view.category == "run" and view.kind != "run.completed"):
            if line_open:
                console.print()
                line_open = False
            render_stream_event_view(view)

    svc = ChatService.from_env(provider=opts.provider)
    result = await svc.chat_stream(
        user_id=opts.user_id,
        session_id=opts.session_id,
        message=message,
        on_event=_on_event,
    )

    while result.waiting_human and result.pending_human_request:
        if line_open:
            console.print()
            line_open = False
        render_approval_request(result.pending_human_request)
        choice = render_approval_prompt()

        edited_args: dict | None = None
        if choice == "e":
            raw = console.input("  Arguments JSON: ").strip()
            try:
                edited_args = json.loads(raw)
            except Exception:
                print_error("JSON 解析失败，按原参数 approve")

        result = await svc.approve_stream(
            run_id=result.run_id,
            decision=HumanDecision(
                approved=choice != "r",
                edited_arguments=edited_args,
            ),
            on_event=_on_event,
        )

    if line_open:
        console.print()

    if not streamed_any:
        console.print(f"assistant> {result.final_output or result.failure_reason or ''}")

    console.print(f"run_id=[dim]{result.run_id}[/]  status=[bold]{result.status}[/]")
    cost_summary = result.cost_summary or latest_cost
    if isinstance(cost_summary, dict) and cost_summary.get("total_tokens"):
        inp = cost_summary.get("input_tokens", 0)
        out = cost_summary.get("output_tokens", 0)
        total = cost_summary.get("total_tokens", 0)
        console.print(f"[dim]tokens: in={inp} out={out} total={total}[/]")