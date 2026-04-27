"""
render.py — Rich 渲染工具集

所有 CLI 命令共用，不做任何业务逻辑。
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()
err_console = Console(stderr=True)


# ── 通用 ─────────────────────────────────────────────────────────────────

def print_json(data: Any) -> None:
    """以 JSON 格式输出（--json 模式）。"""
    console.print_json(json.dumps(data, ensure_ascii=False, default=str))


def print_error(msg: str) -> None:
    err_console.print(f"[bold red]error:[/] {msg}")


def print_success(msg: str) -> None:
    console.print(f"[bold green]✓[/] {msg}")


def print_info(msg: str) -> None:
    console.print(f"[dim]{msg}[/]")


# ── run / chat ────────────────────────────────────────────────────────────

def render_chat_result(result: Any, *, show_cost: bool = True) -> None:
    """渲染 ChatRunResult。"""
    status_color = {
        "completed": "green",
        "degraded": "yellow",
        "failed": "red",
        "waiting_human": "orange1",
        "cancelled": "dim",
    }.get(result.status, "white")

    output = result.final_output or result.failure_reason or ""
    console.print(Panel(output, title=f"[{status_color}]{result.status}[/]", border_style=status_color))
    console.print(f"  run_id=[dim]{result.run_id}[/]")
    if show_cost:
        _render_cost(result.cost_summary)


def render_approval_request(pending: dict[str, Any]) -> None:
    """渲染审批请求面板。"""
    tool_name = pending.get("context", {}).get("tool_name", "tool")
    risk = pending.get("context", {}).get("risk_level", "unknown")
    risk_color = {"high": "red", "medium": "yellow", "low": "green"}.get(risk, "white")

    console.print(Panel(
        json.dumps(pending, ensure_ascii=False, indent=2),
        title=f"[bold]Approval Required[/] · [bold {risk_color}]{tool_name}[/] (risk: {risk})",
        border_style="yellow",
    ))


def render_approval_prompt() -> str:
    """交互式审批提示，返回 'a' / 'r' / 'e'。"""
    console.print(
        "  [bold green]a[/] approve  "
        "[bold red]r[/] reject  "
        "[bold yellow]e[/] edit arguments"
    )
    while True:
        raw = console.input("[bold yellow]>[/] ").strip().lower()
        if raw in {"a", "approve", "r", "reject", "e", "edit"}:
            return raw[0]
        console.print("[dim]请输入 a / r / e[/]")


# ── plan runs ─────────────────────────────────────────────────────────────

def render_plan_runs_table(plan_runs: list[dict[str, Any]]) -> None:
    """渲染 plan run 列表表格。"""
    if not plan_runs:
        console.print("[dim]No plan runs found.[/]")
        return
    t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    t.add_column("plan_run_id", style="dim", no_wrap=True)
    t.add_column("goal", max_width=50)
    t.add_column("status", justify="center")
    t.add_column("steps", justify="right")
    t.add_column("done", justify="right")
    for pr in plan_runs:
        st = pr.get("status", "")
        st_color = {
            "completed": "green", "failed": "red",
            "waiting_human": "yellow", "running": "blue",
        }.get(st, "white")
        t.add_row(
            pr.get("plan_run_id", "")[:20],
            pr.get("goal", "")[:50],
            f"[{st_color}]{st}[/]",
            str(pr.get("total_steps", "")),
            str(pr.get("completed_steps", "")),
        )
    console.print(t)


def render_plan_run_detail(pr: dict[str, Any]) -> None:
    """渲染单个 plan run 的步骤树。"""
    step_runs = pr.get("step_runs") or []
    console.print(f"\n[bold]plan_run_id[/]: {pr.get('plan_run_id')}")
    console.print(f"[bold]status[/]: {pr.get('status')}")
    console.print(f"[bold]goal[/]: {pr.get('goal', '')}\n")
    if not step_runs:
        console.print("[dim]No step runs.[/]")
        return
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    t.add_column("#", justify="right", width=3)
    t.add_column("step_id", style="dim")
    t.add_column("title", max_width=40)
    t.add_column("status", justify="center")
    for i, sr in enumerate(step_runs, 1):
        st = sr.get("status", "")
        st_color = {
            "completed": "green", "failed": "red",
            "waiting_human": "yellow", "running": "blue",
        }.get(st, "white")
        t.add_row(
            str(i),
            str(sr.get("step_id", ""))[:20],
            str(sr.get("title", "") or sr.get("step_id", ""))[:40],
            f"[{st_color}]{st}[/]",
        )
    console.print(t)


# ── plans ─────────────────────────────────────────────────────────────────

def render_plans_table(plans: list[dict[str, Any]]) -> None:
    if not plans:
        console.print("[dim]No plans found.[/]")
        return
    t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    t.add_column("plan_id", style="dim", no_wrap=True)
    t.add_column("goal", max_width=60)
    t.add_column("steps", justify="right")
    t.add_column("status", justify="center")
    for p in plans:
        t.add_row(
            p.get("plan_id", "")[:20],
            p.get("goal", "")[:60],
            str(p.get("steps", "")),
            p.get("status", ""),
        )
    console.print(t)


def render_plan_detail(plan: dict[str, Any]) -> None:
    steps = plan.get("steps") or []
    console.print(f"\n[bold]plan_id[/]: {plan.get('plan_id')}")
    console.print(f"[bold]goal[/]: {plan.get('goal', '')}\n")
    for i, s in enumerate(steps, 1):
        title = s.get("title") or s.get("description", "")[:60]
        deps = ", ".join(s.get("dependencies", [])) or "—"
        console.print(f"  [bold]{i}.[/] {title}")
        console.print(f"     deps=[dim]{deps}[/]  status=[dim]{s.get('status', '')}[/]")


# ── memory ────────────────────────────────────────────────────────────────

def render_memories_table(memories: list[dict[str, Any]]) -> None:
    if not memories:
        console.print("[dim]No memories found.[/]")
        return
    t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    t.add_column("id", style="dim", width=12)
    t.add_column("type", justify="center", width=12)
    t.add_column("content", max_width=70)
    t.add_column("tags", max_width=20)
    for m in memories:
        mem_type = m.get("memory_type", "")
        type_color = {"episode": "cyan", "semantic": "magenta", "procedural": "yellow"}.get(mem_type, "white")
        t.add_row(
            m.get("id", "")[:12],
            f"[{type_color}]{mem_type}[/]",
            m.get("content", "")[:70],
            ", ".join(m.get("tags", []))[:20],
        )
    console.print(t)


# ── cost ──────────────────────────────────────────────────────────────────

def _render_cost(cost_summary: dict[str, Any]) -> None:
    if not cost_summary:
        return
    total = cost_summary.get("total_tokens", 0)
    if not total:
        return
    inp = cost_summary.get("input_tokens", 0)
    out = cost_summary.get("output_tokens", 0)
    cost_usd = cost_summary.get("cost_usd", 0.0)
    cost_cny = cost_summary.get("cost_cny", 0.0)
    calls = cost_summary.get("model_calls", 0)
    if cost_usd == 0:
        console.print(f"  [dim]tokens: in={inp} out={out} total={total} | 本地免费 ({calls} calls)[/]")
    else:
        console.print(f"  [dim]tokens: in={inp} out={out} total={total} | ¥{cost_cny:.6f} / ${cost_usd:.6f} ({calls} calls)[/]")
