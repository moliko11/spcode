"""
commands/runs.py — run 查询与监控

  agent runs list [--status running] [--limit 20]
  agent runs show <run_id>
  agent runs watch <run_id>      # 实时订阅事件流
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any, Optional

import httpx
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
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="远端 API 地址，例如 http://127.0.0.1:8000", envvar="AGENT_API_URL"),
    ] = None,
) -> None:
    """
    实时监控 plan run 事件流，直到完成或超时。

    本地模式：直接 poll plan_run_store；
    Remote 模式（配置 AGENT_API_URL）：订阅 SSE。
    """
    asyncio.run(_watch(plan_run_id, timeout, api_url=api_url))


async def _watch(plan_run_id: str, timeout: int, api_url: str | None = None) -> None:
    if api_url:
        await _watch_remote(plan_run_id, timeout, api_url)
        return

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


async def _watch_remote(run_id: str, timeout: int, api_url: str) -> None:
    console.print(f"[dim]Watching run_id={run_id} via SSE  (Ctrl+C to stop)[/]\n")
    async for event in _stream_remote_events(api_url, run_id, timeout):
        kind = str(event.get("event_kind") or event.get("kind") or event.get("event_type") or "unknown")
        _render_run_event(event)
        if kind in {"run.completed", "run.failed", "run.cancelled", "run.waiting_human", "run.degraded"}:
            return


async def _stream_remote_events(api_url: str, run_id: str, timeout: int) -> AsyncIterator[dict[str, Any]]:
    url = _build_run_events_url(api_url, run_id)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout)) as client:
        async with client.stream("GET", url, headers={"Accept": "text/event-stream"}) as response:
            response.raise_for_status()
            async for event in _parse_sse_events(response.aiter_lines()):
                yield event


def _build_run_events_url(api_url: str, run_id: str) -> str:
    base = api_url.rstrip("/")
    if base.endswith("/api"):
        return f"{base}/events/runs/{run_id}"
    return f"{base}/api/events/runs/{run_id}"


async def _parse_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    current_event = "message"
    data_lines: list[str] = []

    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                payload = "\n".join(data_lines)
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    event = {"kind": current_event, "payload": payload}
                if "kind" not in event and current_event != "message":
                    event["kind"] = current_event
                yield event
            current_event = "message"
            data_lines = []
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip() or "message"
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())


def _render_run_event(event: dict[str, Any]) -> None:
    kind = str(event.get("event_kind") or event.get("kind") or event.get("event_type") or "unknown")
    payload = event.get("payload") or {}

    if kind == "heartbeat":
        return
    if kind == "model.token":
        token = payload.get("token") or payload.get("delta")
        if token:
            console.print(f"[cyan]token[/] {token}")
        return
    if kind == "model.thinking":
        text = payload.get("text") or payload.get("thinking") or ""
        if text:
            console.print(f"[magenta]thinking[/] {text}")
        return
    if kind == "tool.pending_approval":
        tool_name = payload.get("tool_name") or payload.get("name") or "tool"
        console.print(f"[yellow]approval[/] waiting for {tool_name}")
        return
    if kind == "model.usage" or kind == "run.token_budget":
        total = payload.get("total_tokens") or payload.get("budget_total_tokens") or 0
        if total:
            console.print(f"[dim]{kind}: total_tokens={total}[/]")
        else:
            console.print(f"[dim]{kind}[/]")
        return
    if kind.startswith("run."):
        summary = event.get("final_output") or event.get("error") or payload.get("final_output") or ""
        if summary:
            console.print(f"[bold]{kind}[/] {summary}")
        else:
            console.print(f"[bold]{kind}[/]")
        return
    if kind == "run.started":
        goal = event.get("goal") or payload.get("task") or ""
        console.print(f"[green]run.started[/] {goal}")
        return

    step = event.get("step")
    if step is None:
        console.print(f"[dim]{kind}[/]")
    else:
        console.print(f"[dim]{kind}[/] step={step}")
