"""
commands/approvals.py — 审批管理

  agent approvals list              # 列出所有待审批的 plan run
  agent approve <plan_run_id>       # 审批通过（交互式）
  agent approve <plan_run_id> --args '{"path":"..."}' # 带编辑参数
  agent reject  <plan_run_id> [--reason "..."]
  agent recover <plan_run_id>       # 从失败状态恢复
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Optional

import typer

from packages.cli.options import JsonOpt, ProviderOpt, UserIdOpt
from packages.cli.render import (
    console,
    print_error,
    print_json,
    print_success,
    render_approval_request,
    render_plan_run_detail,
)

app = typer.Typer(help="处理待审批操作")


@app.command("list")
def list_approvals_cmd(
    json_output: JsonOpt = False,
) -> None:
    """列出所有处于 waiting_human 状态的 plan run。"""
    from packages.app_service.query_service import QueryService
    from packages.cli.render import render_plan_runs_table

    qs = QueryService.from_env()
    waiting = qs.list_plan_runs(status_filter="waiting_human", limit=50)

    if not waiting:
        console.print("[dim]No pending approvals.[/]")
        return

    if json_output:
        print_json(waiting)
    else:
        render_plan_runs_table(waiting)
        console.print(f"\n[bold yellow]{len(waiting)} pending[/]  →  run: agent approve <plan_run_id>")


@app.command("approve")
def approve_cmd(
    plan_run_id: Annotated[str, typer.Argument(help="plan_run_id")],
    args: Annotated[Optional[str], typer.Option("--args", help="编辑后的 JSON 参数（覆盖原始参数）")] = None,
    provider: ProviderOpt = "openai_compatible",
    user_id: UserIdOpt = "demo-user",
    json_output: JsonOpt = False,
) -> None:
    """
    审批通过一个 waiting_human plan run 并继续执行。

    示例：
      agent approve plan-run-abc123
      agent approve plan-run-abc123 --args '{"path": "new_file.py", "content": "..."}'
    """
    asyncio.run(_approve(plan_run_id, args, provider, user_id, json_output))


async def _approve(
    plan_run_id: str,
    args: str | None,
    provider: str,
    user_id: str,
    json_output: bool,
) -> None:
    import os
    from packages.app_service.orchestrate_service import OrchestrateService

    if provider:
        os.environ["MOLIKO_LLM_PROVIDER"] = provider

    edited_arguments: dict | None = None
    if args:
        try:
            edited_arguments = json.loads(args)
        except json.JSONDecodeError as e:
            print_error(f"--args JSON 解析失败: {e}")
            raise typer.Exit(1)

    svc = OrchestrateService.from_env(provider=provider, user_id=user_id)

    # 先显示待审批内容
    qs_mod = __import__("packages.app_service.query_service", fromlist=["QueryService"])
    qs = qs_mod.QueryService.from_env()
    pr = qs.get_plan_run(plan_run_id)
    if pr:
        ws = pr.get("waiting_step") or {}
        pending = ws.get("pending_human_request") if isinstance(ws, dict) else None
        if pending:
            render_approval_request(pending)

    with console.status("[bold green]Approving...[/]"):
        summary = await svc.approve(
            plan_run_id=plan_run_id,
            approved=True,
            edited_arguments=edited_arguments,
        )

    # 审批后可能继续执行，再循环处理
    while summary.waiting_human and summary.waiting_step:
        from packages.cli.render import render_approval_prompt
        ws = summary.waiting_step or {}
        pending = ws.get("pending_human_request", {})
        render_approval_request(pending)
        choice = render_approval_prompt()

        ea = None
        if choice == "e":
            raw = console.input("  Arguments JSON: ").strip()
            try:
                ea = json.loads(raw)
            except Exception:
                pass

        approved = choice != "r"
        with console.status("[bold blue]Resuming...[/]"):
            summary = await svc.approve(
                plan_run_id=summary.plan_run_id,
                approved=approved,
                edited_arguments=ea,
            )

    if json_output:
        print_json(summary.raw)
    else:
        render_plan_run_detail(summary.raw)
        print_success(f"plan_run {plan_run_id} → {summary.status}")


@app.command("reject")
def reject_cmd(
    plan_run_id: Annotated[str, typer.Argument(help="plan_run_id")],
    reason: Annotated[str, typer.Option("--reason", help="拒绝原因")] = "rejected by user",
    provider: ProviderOpt = "openai_compatible",
    user_id: UserIdOpt = "demo-user",
) -> None:
    """拒绝一个 waiting_human plan run。"""
    asyncio.run(_reject(plan_run_id, reason, provider, user_id))


async def _reject(plan_run_id: str, reason: str, provider: str, user_id: str) -> None:
    import os
    from packages.app_service.orchestrate_service import OrchestrateService

    if provider:
        os.environ["MOLIKO_LLM_PROVIDER"] = provider

    svc = OrchestrateService.from_env(provider=provider, user_id=user_id)
    with console.status("[bold red]Rejecting...[/]"):
        summary = await svc.approve(
            plan_run_id=plan_run_id,
            approved=False,
        )
    console.print(f"[bold red]rejected[/]  plan_run_id={plan_run_id}  status={summary.status}")


@app.command("recover")
def recover_cmd(
    plan_run_id: Annotated[str, typer.Argument(help="plan_run_id")],
    provider: ProviderOpt = "openai_compatible",
    user_id: UserIdOpt = "demo-user",
    json_output: JsonOpt = False,
) -> None:
    """从失败状态恢复一个 plan run（重试失败步骤）。"""
    asyncio.run(_recover(plan_run_id, provider, user_id, json_output))


async def _recover(plan_run_id: str, provider: str, user_id: str, json_output: bool) -> None:
    import os
    from packages.app_service.orchestrate_service import OrchestrateService

    if provider:
        os.environ["MOLIKO_LLM_PROVIDER"] = provider

    svc = OrchestrateService.from_env(provider=provider, user_id=user_id)
    with console.status("[bold blue]Recovering...[/]"):
        summary = await svc.recover(plan_run_id=plan_run_id)

    if json_output:
        print_json(summary.raw)
    else:
        render_plan_run_detail(summary.raw)
        print_success(f"plan_run {plan_run_id} recovered → {summary.status}")
