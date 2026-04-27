"""
commands/chat.py — chat 子命令

  agent chat "message"
  agent chat --session-id demo --provider mock "message"
  agent chat --print "message"     # 非交互，输出后退出（脚本用）
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Optional

import typer

from packages.cli.options import GlobalOptions, JsonOpt, ProviderOpt, SessionIdOpt, UserIdOpt
from packages.cli.render import (
    console,
    print_error,
    print_json,
    render_approval_prompt,
    render_approval_request,
    render_chat_result,
)

app = typer.Typer(help="发送消息给 Agent，支持多轮对话")


@app.callback(invoke_without_command=True)
def chat_cmd(
    ctx: typer.Context,
    message: Annotated[Optional[str], typer.Argument(help="发送给 agent 的消息")] = None,
    provider: ProviderOpt = "openai_compatible",
    user_id: UserIdOpt = "demo-user",
    session_id: SessionIdOpt = "demo-session",
    json_output: JsonOpt = False,
    print_mode: Annotated[bool, typer.Option("--print", "-p", is_flag=True, help="非交互一次性输出")] = False,
) -> None:
    """
    发送一条消息并等待结果。

    示例：
      agent chat "列出 runtime 目录结构"
      agent chat --session-id my-session "继续上次的任务"
    """
    if message is None:
        console.print("[dim]用法: agent chat [OPTIONS] MESSAGE[/]")
        raise typer.Exit(0)

    opts = GlobalOptions(
        provider=provider,
        user_id=user_id,
        session_id=session_id,
        json_output=json_output,
    )
    asyncio.run(_do_chat(opts, message))


async def _do_chat(opts: GlobalOptions, message: str) -> None:
    """实际执行 chat（供 REPL 和命令模式共用）。"""
    from packages.app_service.chat_service import ChatService, HumanDecision

    import os
    if opts.provider:
        os.environ["MOLIKO_LLM_PROVIDER"] = opts.provider

    svc = ChatService.from_env(provider=opts.provider)

    with console.status("[bold blue]Thinking...[/]"):
        result = await svc.chat(
            user_id=opts.user_id,
            session_id=opts.session_id,
            message=message,
        )

    # 审批循环
    while result.waiting_human and result.pending_human_request:
        render_approval_request(result.pending_human_request)
        choice = render_approval_prompt()

        edited_args: dict | None = None
        if choice == "e":
            import json
            raw = console.input("  Arguments JSON: ").strip()
            try:
                edited_args = json.loads(raw)
            except Exception:
                print_error("JSON 解析失败，按原参数 approve")

        approved = choice != "r"
        with console.status("[bold blue]Resuming...[/]"):
            result = await svc.approve(
                run_id=result.run_id,
                decision=HumanDecision(
                    approved=approved,
                    edited_arguments=edited_args,
                ),
            )

    if opts.json_output:
        from dataclasses import asdict
        print_json(result.__dict__)
    else:
        render_chat_result(result)
