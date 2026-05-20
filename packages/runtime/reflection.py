from __future__ import annotations

"""
reflection.py — 轻量级自我反思引擎

不调用 LLM，纯规则分析，零额外延迟：
  - reflect_on_failure: 分析工具失败原因并给出下一步建议
  - reflect_on_loop:    检测重复行为并给出破局建议
  - pre_finalize_check: 输出前自检，是否真正回答了问题
"""

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import AgentState


@dataclass
class ReflectionNote:
    category: str          # "failure" | "loop" | "incomplete"
    message: str           # 人类可读的分析
    suggestion: str        # 建议的下一步动作或策略
    severity: str = "info" # "info" | "warning" | "critical"

    def to_prompt_text(self) -> str:
        return (
            f"[Reflection/{self.category}] {self.message}\n"
            f"Suggestion: {self.suggestion}"
        )


class ReflectionEngine:
    """
    规则驱动的自我反思引擎。
    全部方法同步，随时可在 async 上下文内调用。
    """

    # 最多追溯多少步检测重复
    LOOP_WINDOW = 6
    # 同名工具+参数完全相同出现几次算"重复"
    LOOP_THRESHOLD = 2
    # 工具名前缀 → 错误归类
    _FILE_TOOLS = frozenset({"file_read", "file_write", "file_edit", "glob", "grep", "list_dir"})
    _WEB_TOOLS = frozenset({"web_search", "web_fetch"})
    _SHELL_TOOLS = frozenset({"bash"})
    _CODE_TOOLS = frozenset({"run_tests", "lint", "git_diff", "git_log", "find_symbol"})

    # -------------------------------------------------------------------
    # 公开接口
    # -------------------------------------------------------------------

    def reflect_on_failure(self, state: "AgentState", error: Exception) -> ReflectionNote:
        """
        工具失败后分析：是参数错了？路径错了？逻辑错了？
        返回一条可注入系统提示词的反思笔记。
        """
        error_text = str(error).lower()
        tool_name = self._last_tool_name(state)

        # 路径/文件不存在
        if any(kw in error_text for kw in ("not found", "no such file", "filenotfounderror", "does not exist")):
            return ReflectionNote(
                category="failure",
                message=f"Tool '{tool_name}' failed: path or file does not exist.",
                suggestion=(
                    "Verify the path with `glob` or `list_dir` before retrying. "
                    "Check for typos or workspace root prefix issues."
                ),
                severity="warning",
            )

        # 权限/沙箱拒绝
        if any(kw in error_text for kw in ("permission denied", "guardrail", "blocked", "not allowed", "outside workspace")):
            return ReflectionNote(
                category="failure",
                message=f"Tool '{tool_name}' was blocked by permission or guardrail.",
                suggestion=(
                    "Check whether the path is inside the workspace root. "
                    "For high-risk tools, await human approval or use a safer alternative."
                ),
                severity="warning",
            )

        # 超时
        if any(kw in error_text for kw in ("timeout", "timed out", "deadline")):
            return ReflectionNote(
                category="failure",
                message=f"Tool '{tool_name}' timed out.",
                suggestion=(
                    "Try narrowing the scope (fewer files, shorter time window) or "
                    "increase the timeout argument if the tool supports it."
                ),
                severity="warning",
            )

        # 语法/解析错误
        if any(kw in error_text for kw in ("syntaxerror", "json", "parse", "invalid", "malformed")):
            return ReflectionNote(
                category="failure",
                message=f"Tool '{tool_name}' encountered a parse or syntax error.",
                suggestion=(
                    "Double-check the argument format. For file_edit, ensure old_str "
                    "matches the file content exactly including whitespace."
                ),
                severity="warning",
            )

        # 工具专属分析
        if tool_name in self._FILE_TOOLS:
            return ReflectionNote(
                category="failure",
                message=f"File tool '{tool_name}' failed: {error}",
                suggestion="Use `file_read` first to confirm the file content, then retry with corrected arguments.",
                severity="warning",
            )

        if tool_name in self._CODE_TOOLS:
            return ReflectionNote(
                category="failure",
                message=f"Code tool '{tool_name}' failed: {error}",
                suggestion=(
                    "Check that the test runner or linter is installed in the current environment. "
                    "Narrow the target path or run with no arguments to verify the tool works."
                ),
                severity="info",
            )

        # 通用
        return ReflectionNote(
            category="failure",
            message=f"Tool '{tool_name}' failed with: {type(error).__name__}: {error}",
            suggestion="Analyze the error message, adjust arguments, and retry. If the same tool keeps failing, consider an alternative approach.",
            severity="info",
        )

    def reflect_on_loop(self, state: "AgentState") -> ReflectionNote | None:
        """
        检测 tool_results 中是否有重复的工具+参数组合。
        如果没有检测到重复则返回 None。
        """
        recent = state.tool_results[-self.LOOP_WINDOW:]
        if len(recent) < self.LOOP_THRESHOLD:
            return None

        # 统计 (tool_name, 参数摘要) 的频率
        key_counts: Counter[str] = Counter()
        for result in recent:
            name = result.tool_name or ""
            # 用参数的前 120 字符作为摘要（避免大参数对比失误）
            # ToolResult 的参数存在 metadata["arguments"]
            args = result.metadata.get("arguments", {})
            args_summary = str(args)[:120]
            key_counts[f"{name}:{args_summary}"] += 1

        repeated = [(k, v) for k, v in key_counts.items() if v >= self.LOOP_THRESHOLD]
        if not repeated:
            return None

        top_key, top_count = max(repeated, key=lambda x: x[1])
        tool_name = top_key.split(":", 1)[0]
        return ReflectionNote(
            category="loop",
            message=f"Detected repetitive tool call: '{tool_name}' called {top_count} times with similar arguments in the last {self.LOOP_WINDOW} steps.",
            suggestion=(
                "Stop repeating the same call. Try: "
                "(1) refine arguments, "
                "(2) switch to a different tool, "
                "(3) synthesize an answer from existing observations, "
                "(4) ask for clarification if the goal is unclear."
            ),
            severity="warning",
        )

    def pre_finalize_check(self, state: "AgentState") -> ReflectionNote | None:
        """
        输出前自检：final_output 是否真正回答了问题？
        只在输出明显不完整时返回 warning，正常情况返回 None。
        """
        output = (state.final_output or "").strip()
        task = (state.task or "").strip()

        # 输出为空
        if not output:
            return ReflectionNote(
                category="incomplete",
                message="Final output is empty.",
                suggestion="Generate a summary of completed work or an explicit answer before finalizing.",
                severity="critical",
            )

        # 输出极短（<15字符）且任务非空
        if len(output) < 15 and task:
            return ReflectionNote(
                category="incomplete",
                message=f"Final output is very short ({len(output)} chars) for task: '{task[:80]}'.",
                suggestion="Provide a more complete answer or summary. If the task is truly trivial, this is fine.",
                severity="info",
            )

        # 检测是否以"I will..."/"我将..."开头（表示还没实际做）
        lower_output = output.lower()
        planning_markers = ("i will ", "i'll ", "i am going to ", "我将", "我会", "我打算", "接下来我")
        if any(lower_output.startswith(m) for m in planning_markers):
            return ReflectionNote(
                category="incomplete",
                message="Final output starts with a planning statement instead of actual results.",
                suggestion="Execute the plan and provide concrete results/observations instead of a forward-looking statement.",
                severity="warning",
            )

        return None

    # -------------------------------------------------------------------
    # 私有辅助
    # -------------------------------------------------------------------

    def _last_tool_name(self, state: "AgentState") -> str:
        if state.tool_results:
            return state.tool_results[-1].tool_name or "unknown"
        return "unknown"
