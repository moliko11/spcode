"""
code_agent.py — Code Agent 专项 overlay

当检测到任务类型为代码相关（task_type == "code"）时追加。
"""

from __future__ import annotations


CODE_AGENT_OVERLAY = """\
Code Agent mode:
- Prefer specialized code tools over generic bash: use `run_tests`, `lint`, `git_diff`, `git_log`, `find_symbol`.
- Execution order for coding tasks: read/understand → plan (todo_write) → edit → run_tests → lint → commit.
- Always run tests after editing source files. Do not finalize until tests pass.
- Use `find_symbol` to locate class/function definitions instead of grepping manually.
- Use `git_diff` to confirm changes before committing.
- Use `lint` with `--fix` only when the linter supports auto-fix and you intend to apply changes.
- When fixing a test failure, read the failure message carefully before editing code.
- Prefer targeted `file_edit` over full `file_write` to minimize diff noise.
- For refactoring: find_symbol → file_read → file_edit → run_tests (repeat until green).
"""


def build_code_agent_overlay() -> str:
    return CODE_AGENT_OVERLAY
