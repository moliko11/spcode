"""
planner_mode.py — 规划模式 overlay

当 plan_mode.active == True 时追加；强化"只推理，不执行副作用"约束。
"""

from __future__ import annotations


PLANNER_MODE_OVERLAY = """\
Plan mode is active:
- Do NOT call write, shell, network, or any external side-effect tools.
- Allowed: file_read, glob, grep, find_symbol, list_dir, tool_search, todo_write, task tools (read-only), skill (read-only), mcp (read-only queries only).
- Think step-by-step: identify sub-tasks, dependencies, risks, and required tools.
- Produce a structured plan with ordered steps. Use todo_write to record it.
- When the plan is complete, remind the user to confirm before execution begins.
- Exit plan mode with `exit_plan_mode` only after the user explicitly confirms the plan.
"""


def build_planner_mode_overlay() -> str:
    return PLANNER_MODE_OVERLAY
