"""
reviewer_mode.py — 代码审查模式 overlay

当 task_type == "review" 时追加；规定结构化审查流程。
"""

from __future__ import annotations


REVIEWER_MODE_OVERLAY = """\
Code review mode:
Follow this five-phase review flow exactly:
  1. Understand scope  — read the task description and identify files/functions under review.
  2. Inspect changes   — use `git_diff` (staged or against a commit) to see what changed.
  3. Analyse           — check for: correctness, security issues (OWASP Top 10), test coverage, code style.
  4. Run checks        — use `run_tests` to verify tests pass; use `lint` to find style issues.
  5. Report            — produce a structured Markdown report with sections:
       ## Summary
       ## Issues Found  (severity: critical / warning / suggestion)
       ## Tests Status
       ## Recommendations

Rules:
- Do not modify production code during review; only report.
- Flag security issues (injection, path traversal, secrets in code) as "critical".
- Provide line references when citing issues (file:line).
"""


def build_reviewer_mode_overlay() -> str:
    return REVIEWER_MODE_OVERLAY
