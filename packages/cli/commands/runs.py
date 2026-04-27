"""
commands/runs.py — run 查询与监控

  agent runs list [--status running] [--limit 20]
  agent runs show <run_id>
  agent runs watch <run_id>      # 实时订阅事件流
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Optional

import typer

from packages.cli.options import GlobalOptions, JsonOpt, UserIdOpt
from packages.cli.render import (
    console,
    print_error,
    print_json,
    render_plan_runs_table,
    render_plan_run_detail,
    _render_cost,
)

app = typer.Typer(help="查看 / 监控 agent run 历史")


@app.command("list")
def list_runs_cmd(
    status: Annotated[Optional[str], typer.Option("--status", help="过滤状态 running|completed|failed|waiting_human")] = None,
    limit: Annotated[int, typer.Option("--limit", help="最多显示条数")] = 20,
    json_output: JsonOpt = False,
) -> None:
    """列出最近的 plan run（包含状态、步骤数、目标）。"""
    opts = GlobalOptions(json_output=json_output)
    _list_runs(opts, status=status, limit=limit)


def _list_runs(
    opts: GlobalOptions,
    status: str | None = None,
    limit: int = 20,
) -> None:
    from packages.app_service.query_service import QueryService

    qs = QueryService.from_env()
    runs = qs.list_plan_runs(limit=limit, status_filter=status)

    if opts.json_output:
        print_json(runs)
    else:
        render_plan_runs_table(runs)


@app.command("show")
def show_run_cmd(
    plan_run_id: Annotated[str, typer.Argument(help="plan_run_id")],
    json_output: JsonOpt = False,
) -> None:
    """显示单个 plan run 的步骤详情。"""
    from packages.app_service.query_service import QueryService

    qs = QueryService.from_env()
    pr = qs.get_plan_run(plan_run_id)
    if pr is None:
        print_error(f"plan_run {plan_run_id!r} not found")
        raise typer.Exit(1)

    if json_output:
        print_json(pr)
    else:
        render_plan_run_detail(pr)
        cost = pr.get("cost_summary", {})
        if cost:
            _render_cost(cost)


@app.command("watch")
def watch_cmd(
    plan_run_id: Annotated[str, typer.Argument(help="plan_run_id 或 run_id")],
    timeout: Annotated[int, typer.Option("--timeout", help="最长等待秒数")] = 300,
) -> None:
    """
    实时监控 plan run 事件流，直到完成或超时。

    本地模式：直接 poll plan_run_store；
    Remote 模式（配置 AGENT_API_URL）：订阅 SSE。
    """
    asyncio.run(_watch(plan_run_id, timeout))


async def _watch(plan_run_id: str, timeout: int) -> None:
    from packages.app_service.query_service import QueryService
    from packages.cli.render import render_plan_run_detail

    qs = QueryService.from_env()
    start = time.time()

    console.print(f"[dim]Watching plan_run_id={plan_run_id}  (Ctrl+C to stop)[/]\n")

    prev_status = None
    while True:
        pr = qs.get_plan_run(plan_run_id)
        if pr is None:
            print_error(f"plan_run {plan_run_id!r} not found")
            return

        status = pr.get("status", "")
        if status != prev_status:
            render_plan_run_detail(pr)
            prev_status = status

        if status in ("completed", "failed", "cancelled"):
            console.print(f"\n[bold]plan_run 已结束: {status}[/]")
            return

        if time.time() - start > timeout:
            console.print("[yellow]watch timeout，run 仍在后台继续[/]")
            return

        await asyncio.sleep(2)
