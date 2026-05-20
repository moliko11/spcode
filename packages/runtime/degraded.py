from __future__ import annotations

"""
degraded.py — 预算耗尽后的降级处理器

当 BudgetExceeded 被捕获时，DegradedHandler：
  1. 收集已完成工具结果的部分输出
  2. 合成 "best-effort" 摘要
  3. 将 state.status 置为 DEGRADED（而不是 FAILED）
  4. 填充 state.final_output，确保用户能看到部分结果

这样 agent 在预算耗尽时仍能输出有意义的答案，而不是空手而归。
"""

from typing import TYPE_CHECKING

from .models import RunStatus

if TYPE_CHECKING:
    from .models import AgentState, ToolResult


class DegradedHandler:
    """
    预算耗尽降级处理器。

    在 agent_loop 的 BudgetExceeded 捕获点调用：
        degraded = DegradedHandler()
        degraded.handle(state, budget_error)
    """

    # 每个工具结果最多取多少字符进入摘要
    RESULT_SNIPPET_LEN = 400
    # 最多汇总多少个工具结果
    MAX_RESULTS = 8

    def handle(self, state: "AgentState", error: Exception) -> None:
        """
        就地修改 state：设置 DEGRADED 状态 + best-effort final_output。
        """
        partial_summary = self._build_partial_summary(state)

        state.status = RunStatus.DEGRADED  # type: ignore[attr-defined]

        existing = (state.final_output or "").strip()
        if existing:
            # 已有输出（比如已经写了一半），保留并追加降级提示
            state.final_output = (
                f"{existing}\n\n"
                f"---\n"
                f"[Degraded: budget exhausted — partial results above]\n"
            )
        else:
            state.final_output = (
                f"Budget exhausted before task completion. "
                f"Partial results collected:\n\n"
                f"{partial_summary}\n\n"
                f"[Degraded: {error}]"
            )

    # ----------------------------------------------------------------
    # 私有辅助
    # ----------------------------------------------------------------

    def _build_partial_summary(self, state: "AgentState") -> str:
        results = state.tool_results or []
        if not results:
            return "(no tool results collected)"

        # 只取最近 MAX_RESULTS 个，优先有输出的
        candidates = [r for r in results if r.output and str(r.output).strip()]
        if not candidates:
            candidates = list(results)
        recent = candidates[-self.MAX_RESULTS:]

        lines: list[str] = []
        for i, result in enumerate(recent, 1):
            tool_name = result.tool_name or "unknown"
            ok_flag = "ok" if result.ok else "failed"
            output_str = str(result.output or "").strip()
            snippet = output_str[: self.RESULT_SNIPPET_LEN]
            if len(output_str) > self.RESULT_SNIPPET_LEN:
                snippet += f"… [{len(output_str) - self.RESULT_SNIPPET_LEN} chars truncated]"
            lines.append(f"{i}. [{ok_flag}] {tool_name}: {snippet or '(empty)'}")

        total = len(results)
        shown = len(recent)
        header = f"Completed {total} tool call(s); showing last {shown}:"
        return header + "\n" + "\n".join(lines)
