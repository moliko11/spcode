"""
commands/plans.py — 计划生成与查询

  agent plans create "重构 runtime 模块"
  agent plans list
  agent plans show <plan_id>
  agent plans run <plan_id>       # 基于已有计划启动 orchestrate
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Optional

import typer

from packages.cli.options import GlobalOptions, JsonOpt, ProviderOpt, UserIdOpt
from packages.cli.render import (
    console,
    print_error,
    print_json,
    render_plan_detail,
    render_plans_table,
)

app = typer.Typer(help="生成 / 查看任务计划")


@app.command("create")
def create_plan_cmd(
    goal: Annotated[str, typer.Argument(help="目标描述")],
    context: Annotated[str, typer.Option("--context", help="额外上下文")] = "",
    provider: ProviderOpt = "openai_compatible",
    json_output: JsonOpt = False,
) -> None:
    """
    让 Planner 生成任务计划并保存，不执行。

    示例：
      agent plans create "重构 runtime 模块为独立文件"
    """
    asyncio.run(_create_plan(goal, context, provider, json_output))


async def _create_plan(
    goal: str,
    context: str,
    provider: str,
    json_output: bool,
) -> None:
    from packages.app_service.plan_service import PlanService

    if provider:
        os.environ["MOLIKO_LLM_PROVIDER"] = provider

    svc = PlanService.from_env()
    with console.status("[bold blue]Planning...[/]"):
        result = await svc.create_plan(goal=goal, context=context)

    if json_output:
        print_json(result.raw if hasattr(result, "raw") else result.__dict__)
    else:
        render_plan_detail(result.raw if hasattr(result, "raw") else {})
        console.print(f"\n[dim]plan_id={result.plan_id}  已保存[/]")


@app.command("list")
def list_plans_cmd(
    limit: Annotated[int, typer.Option("--limit", help="最多显示条数")] = 20,
    json_output: JsonOpt = False,
) -> None:
    """列出已保存的计划。"""
    from packages.app_service.query_service import QueryService

    qs = QueryService.from_env()
    plans = qs.list_plans(limit=limit)

    if json_output:
        print_json(plans)
    else:
        render_plans_table(plans)


@app.command("show")
def show_plan_cmd(
    plan_id: Annotated[str, typer.Argument(help="plan_id")],
    json_output: JsonOpt = False,
) -> None:
    """显示计划详情（含步骤和依赖关系）。"""
    from packages.app_service.query_service import QueryService

    qs = QueryService.from_env()
    plan = qs.get_plan(plan_id)
    if plan is None:
        print_error(f"plan {plan_id!r} not found")
        raise typer.Exit(1)

    if json_output:
        print_json(plan)
    else:
        render_plan_detail(plan)


@app.command("run")
def run_plan_cmd(
    goal: Annotated[str, typer.Argument(help="目标描述（会生成新计划并立即执行）")],
    context: Annotated[str, typer.Option("--context", help="额外上下文")] = "",
    provider: ProviderOpt = "openai_compatible",
    user_id: UserIdOpt = "demo-user",
    json_output: JsonOpt = False,
) -> None:
    """
    生成计划并立即执行（plan + orchestrate 一步完成）。

    示例：
      agent plans run "重构 runtime 模块"
    """
    asyncio.run(_run_plan(goal, context, provider, user_id, json_output))


async def _run_plan(
    goal: str,
    context: str,
    provider: str,
    user_id: str,
    json_output: bool,
) -> None:
    from packages.app_service.orchestrate_service import OrchestrateService
    from packages.cli.render import render_plan_run_detail, _render_cost

    if provider:
        os.environ["MOLIKO_LLM_PROVIDER"] = provider

    svc = OrchestrateService.from_env(provider=provider, user_id=user_id)

    with console.status("[bold blue]Orchestrating...[/]"):
        summary = await svc.run(goal=goal, context=context)

    # 审批循环
    while summary.waiting_human and summary.waiting_step:
        from packages.cli.render import render_approval_request, render_approval_prompt
        ws = summary.waiting_step or {}
        pending = ws.get("pending_human_request", {})
        render_approval_request(pending)
        choice = render_approval_prompt()

        edited_args = None
        if choice == "e":
            import json
            raw = console.input("  Arguments JSON: ").strip()
            try:
                edited_args = json.loads(raw)
            except Exception:
                pass

        approved = choice != "r"
        with console.status("[bold blue]Resuming...[/]"):
            summary = await svc.approve(
                plan_run_id=summary.plan_run_id,
                approved=approved,
                edited_arguments=edited_args,
            )

    if json_output:
        print_json(summary.raw)
    else:
        render_plan_run_detail(summary.raw)
        _render_cost(summary.cost_summary)
