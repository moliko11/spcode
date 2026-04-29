"""
render.py — Rich 渲染工具集

所有 CLI 命令共用，不做任何业务逻辑。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()
err_console = Console(stderr=True)


@dataclass(slots=True)
class StreamEventView:
    category: str
    kind: str
    label: str
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    step: int | None = None
    terminal_status: str | None = None


@dataclass(slots=True)
class ToolCallDraft:
    key: str
    name: str = "tool"
    call_id: str | None = None
    index: int | None = None
    args_buffer: str = ""
    announced: bool = False


class StreamToolCallAggregator:
    def __init__(self) -> None:
        self._drafts: dict[str, ToolCallDraft] = {}

    def ingest(self, event: Any) -> tuple[list[StreamEventView], bool]:
        kind = _event_kind(event)
        if kind == "model.tool_call_delta":
            view = self._consume_delta(event)
            return ([view] if view is not None else []), True

        if kind in {
            "model.completed",
            "tool.started",
            "tool.pending_approval",
            "run.completed",
            "run.failed",
            "run.cancelled",
            "run.degraded",
            "run.waiting_human",
        }:
            return self.flush(), False

        return [], False

    def flush(self) -> list[StreamEventView]:
        views = [self._draft_to_view(draft) for draft in self._drafts.values()]
        self._drafts.clear()
        return views

    def _consume_delta(self, event: Any) -> StreamEventView | None:
        payload = _event_payload(event)
        delta = payload.get("delta")
        if not isinstance(delta, dict):
            return None

        key = self._delta_key(delta)
        draft = self._drafts.get(key)
        if draft is None:
            draft = ToolCallDraft(key=key)
            self._drafts[key] = draft

        if delta.get("name"):
            draft.name = str(delta["name"])
        if delta.get("id"):
            draft.call_id = str(delta["id"])
        if isinstance(delta.get("index"), int):
            draft.index = int(delta["index"])

        args_chunk = delta.get("args")
        if args_chunk is None:
            args_chunk = delta.get("arguments")
        if args_chunk:
            draft.args_buffer += str(args_chunk)

        if not draft.announced and (draft.name != "tool" or draft.call_id is not None or draft.index is not None):
            draft.announced = True
            return StreamEventView(
                category="tool_call",
                kind="model.tool_call_delta",
                label="tool_call",
                text=f"{self._draft_display_name(draft)} building arguments...",
                payload=payload,
            )
        return None

    def _draft_to_view(self, draft: ToolCallDraft) -> StreamEventView:
        display_name = self._draft_display_name(draft)
        parsed = _try_parse_json(draft.args_buffer)
        if isinstance(parsed, dict):
            details = _format_tool_call_args(parsed)
            text = f"{display_name} {details}" if details else display_name
        elif draft.args_buffer.strip():
            text = f"{display_name} {_shorten(draft.args_buffer.strip(), 160)}"
        else:
            text = f"{display_name} building arguments..."
        return StreamEventView(
            category="tool_call",
            kind="model.tool_call_delta",
            label="tool_call",
            text=text,
            payload={"name": draft.name, "call_id": draft.call_id, "index": draft.index},
        )

    def _draft_display_name(self, draft: ToolCallDraft) -> str:
        suffix = f"[{draft.index}]" if draft.index is not None else ""
        return f"{draft.name}{suffix}"

    def _delta_key(self, delta: dict[str, Any]) -> str:
        if delta.get("id"):
            return f"id:{delta['id']}"
        if isinstance(delta.get("index"), int):
            return f"index:{delta['index']}"
        return f"anon:{len(self._drafts)}"


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

def build_stream_event_view(event: Any) -> StreamEventView:
    payload = _event_payload(event)
    kind = _event_kind(event)
    step = _event_step(event)

    if kind == "heartbeat":
        return StreamEventView(category="ignore", kind=kind, label=kind, payload=payload, step=step)
    if kind == "model.token":
        token = str(payload.get("token") or payload.get("delta") or "")
        return StreamEventView(category="token", kind=kind, label="token", text=token, payload=payload, step=step)
    if kind == "model.thinking":
        text = str(payload.get("text") or payload.get("thinking") or "")
        return StreamEventView(category="thinking", kind=kind, label="thinking", text=text, payload=payload, step=step)
    if kind == "model.tool_call_delta":
        delta = payload.get("delta")
        text = json.dumps(delta, ensure_ascii=False) if delta is not None else ""
        return StreamEventView(category="tool_call", kind=kind, label="tool_call", text=text, payload=payload, step=step)
    if kind == "tool.pending_approval":
        tool_name = str(payload.get("tool_name") or payload.get("name") or "tool")
        return StreamEventView(category="approval", kind=kind, label="approval", text=f"waiting for {tool_name}", payload=payload, step=step)
    if kind.startswith("tool."):
        tool_name = str(payload.get("tool_name") or payload.get("name") or "tool")
        details: list[str] = []
        if kind == "tool.started":
            risk = payload.get("risk_level")
            if risk:
                details.append(f"risk={risk}")
        elif kind == "tool.progress":
            progress_text = payload.get("message") or payload.get("stdout") or payload.get("stderr") or payload.get("status")
            if progress_text:
                details.append(str(progress_text))
        elif kind in {"tool.completed", "tool.failed"}:
            latency_ms = payload.get("latency_ms")
            if latency_ms:
                details.append(f"latency={latency_ms}ms")
            retry_count = payload.get("retry_count")
            if retry_count:
                details.append(f"retries={retry_count}")
            error = payload.get("error")
            if error:
                details.append(str(error))
        text = tool_name
        if details:
            text = f"{tool_name} | {' | '.join(details)}"
        return StreamEventView(category="tool", kind=kind, label=kind, text=text, payload=payload, step=step)
    if kind in {"model.usage", "run.token_budget"}:
        total = payload.get("total_tokens") or payload.get("budget_total_tokens") or 0
        text = f"total_tokens={total}" if total else ""
        return StreamEventView(category="usage", kind=kind, label=kind, text=text, payload=payload, step=step)
    if kind == "run.started":
        goal = str(_event_field(event, "goal") or payload.get("task") or "")
        return StreamEventView(category="run", kind=kind, label=kind, text=goal, payload=payload, step=step)
    if kind.startswith("run."):
        summary = str(
            _event_field(event, "final_output")
            or _event_field(event, "error")
            or payload.get("final_output")
            or payload.get("error")
            or ""
        )
        return StreamEventView(
            category="run",
            kind=kind,
            label=kind,
            text=summary,
            payload=payload,
            step=step,
            terminal_status=kind.split(".", 1)[1],
        )

    text = f"step={step}" if step is not None else ""
    return StreamEventView(category="generic", kind=kind, label=kind, text=text, payload=payload, step=step)


def render_stream_event_view(view: StreamEventView) -> None:
    if view.category == "ignore":
        return
    if view.category == "token":
        if view.text:
            console.print(f"[cyan]{view.label}[/] {view.text}")
        return
    if view.category == "thinking":
        if view.text:
            console.print(f"[magenta]{view.label}[/] {view.text}")
        return
    if view.category == "tool_call":
        if view.text:
            console.print(f"[blue]{view.label}[/] {view.text}")
        else:
            console.print(f"[blue]{view.label}[/]")
        return
    if view.category == "approval":
        console.print(f"[yellow]{view.label}[/] {view.text}")
        return
    if view.category == "tool":
        color = {
            "tool.started": "blue",
            "tool.progress": "cyan",
            "tool.completed": "green",
            "tool.failed": "red",
            "tool.cached": "green",
            "tool.retried": "yellow",
        }.get(view.kind, "white")
        if view.text:
            console.print(f"[{color}]{view.label}[/] {view.text}")
        else:
            console.print(f"[{color}]{view.label}[/]")
        return
    if view.category == "usage":
        if view.text:
            console.print(f"[dim]{view.label}: {view.text}[/]")
        else:
            console.print(f"[dim]{view.label}[/]")
        return
    if view.category == "run":
        color = {
            "started": "green",
            "completed": "green",
            "degraded": "yellow",
            "waiting_human": "yellow",
            "failed": "red",
            "cancelled": "dim",
            "resumed": "cyan",
        }.get(view.terminal_status or "started", "white")
        if view.text:
            console.print(f"[{color}]{view.label}[/] {view.text}")
        else:
            console.print(f"[{color}]{view.label}[/]")
        return

    if view.text:
        console.print(f"[dim]{view.label}[/] {view.text}")
    else:
        console.print(f"[dim]{view.label}[/]")


def _event_field(event: Any, name: str) -> Any:
    if hasattr(event, name):
        return getattr(event, name)
    if isinstance(event, dict):
        return event.get(name)
    return None


def _event_payload(event: Any) -> dict[str, Any]:
    payload = _event_field(event, "payload")
    return payload if isinstance(payload, dict) else {}


def _event_kind(event: Any) -> str:
    return str(_event_field(event, "event_kind") or _event_field(event, "kind") or _event_field(event, "event_type") or "unknown")


def _event_step(event: Any) -> int | None:
    step = _event_field(event, "step")
    return step if isinstance(step, int) else None


def _try_parse_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _format_tool_call_args(args: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in args.items():
        if isinstance(value, str):
            value_text = value
        else:
            value_text = json.dumps(value, ensure_ascii=False)
        parts.append(f"{key}={_shorten(value_text, 80)}")
    return " ".join(parts)


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."

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
