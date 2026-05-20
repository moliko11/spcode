"""
tests/test_phase4_reflection.py

Phase 4: ReflectionEngine + DegradedHandler + PromptSelector 测试
"""

from __future__ import annotations

import pytest

from packages.runtime.degraded import DegradedHandler
from packages.runtime.models import AgentState, BudgetExceeded, RunStatus, ToolResult
from packages.runtime.reflection import ReflectionEngine, ReflectionNote


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_state(**kwargs) -> AgentState:
    return AgentState(
        run_id="test-run",
        user_id="user",
        session_id="session-1",
        task=kwargs.get("task", "fix the bug"),
        **{k: v for k, v in kwargs.items() if k not in {"task"}},
    )


def _make_tool_result(tool_name: str, ok: bool, error: str = "", output: str = "") -> ToolResult:
    return ToolResult(
        call_id="call-1",
        tool_name=tool_name,
        ok=ok,
        output=output or (None if not ok else "success"),
        error=error,
        metadata={"arguments": {"path": "/some/path"}},
    )


# ===========================================================================
# ReflectionEngine.reflect_on_failure
# ===========================================================================

class TestReflectOnFailure:
    def setup_method(self):
        self.engine = ReflectionEngine()

    def test_file_not_found_gives_path_suggestion(self):
        state = _make_state()
        state.tool_results = [_make_tool_result("file_read", ok=False, error="FileNotFoundError: no such file")]
        note = self.engine.reflect_on_failure(state, Exception("no such file"))
        assert note.category == "failure"
        assert "glob" in note.suggestion or "verify" in note.suggestion.lower()

    def test_permission_denied_gives_guardrail_suggestion(self):
        state = _make_state()
        state.tool_results = [_make_tool_result("bash", ok=False, error="permission denied")]
        note = self.engine.reflect_on_failure(state, Exception("permission denied"))
        assert "permission" in note.suggestion.lower() or "workspace" in note.suggestion.lower()

    def test_timeout_suggests_narrowing(self):
        state = _make_state()
        state.tool_results = [_make_tool_result("run_tests", ok=False, error="TimeoutError: timed out")]
        note = self.engine.reflect_on_failure(state, Exception("timed out"))
        assert "timeout" in note.message.lower() or "scope" in note.suggestion.lower()

    def test_syntax_error_suggests_format_check(self):
        state = _make_state()
        state.tool_results = [_make_tool_result("file_edit", ok=False, error="malformed argument: SyntaxError")]
        note = self.engine.reflect_on_failure(state, Exception("SyntaxError"))
        assert "syntax" in note.message.lower() or "format" in note.suggestion.lower()

    def test_code_tool_failure_gives_code_suggestion(self):
        state = _make_state()
        state.tool_results = [_make_tool_result("lint", ok=False, error="ruff not found")]
        note = self.engine.reflect_on_failure(state, Exception("ruff not found"))
        assert note.category == "failure"
        assert isinstance(note.suggestion, str) and len(note.suggestion) > 0

    def test_generic_failure_returns_generic_note(self):
        state = _make_state()
        state.tool_results = [_make_tool_result("web_fetch", ok=False, error="connection refused")]
        note = self.engine.reflect_on_failure(state, Exception("connection refused"))
        assert note.category == "failure"
        assert "web_fetch" in note.message or "unknown" in note.message

    def test_note_to_prompt_text_includes_suggestion(self):
        state = _make_state()
        state.tool_results = [_make_tool_result("file_read", ok=False, error="not found")]
        note = self.engine.reflect_on_failure(state, Exception("not found"))
        text = note.to_prompt_text()
        assert "Suggestion:" in text
        assert "[Reflection/failure]" in text


# ===========================================================================
# ReflectionEngine.reflect_on_loop
# ===========================================================================

class TestReflectOnLoop:
    def setup_method(self):
        self.engine = ReflectionEngine()

    def test_no_loop_returns_none_for_few_results(self):
        state = _make_state()
        state.tool_results = [_make_tool_result("file_read", ok=True, output="content")]
        result = self.engine.reflect_on_loop(state)
        assert result is None

    def test_no_loop_returns_none_for_varied_tools(self):
        state = _make_state()
        state.tool_results = [
            _make_tool_result("file_read", ok=True, output="a"),
            _make_tool_result("grep", ok=True, output="b"),
            _make_tool_result("glob", ok=True, output="c"),
            _make_tool_result("web_search", ok=True, output="d"),
        ]
        result = self.engine.reflect_on_loop(state)
        assert result is None

    def test_loop_detected_for_same_tool_same_args(self):
        state = _make_state()
        # Create multiple copies with same tool_name and arguments
        state.tool_results = [
            ToolResult(call_id=f"c{i}", tool_name="file_read", ok=True, output="same", metadata={"arguments": {"path": "/same/path.py"}})
            for i in range(3)
        ]
        note = self.engine.reflect_on_loop(state)
        assert note is not None
        assert note.category == "loop"
        assert "file_read" in note.message
        assert "3" in note.message

    def test_loop_note_has_stop_suggestion(self):
        state = _make_state()
        state.tool_results = [
            ToolResult(call_id=f"c{i}", tool_name="grep", ok=True, output="hits", metadata={"arguments": {"pattern": "foo", "path": "."}})
            for i in range(3)
        ]
        note = self.engine.reflect_on_loop(state)
        assert note is not None
        assert "stop" in note.suggestion.lower() or "retry" in note.suggestion.lower() or "switch" in note.suggestion.lower()


# ===========================================================================
# ReflectionEngine.pre_finalize_check
# ===========================================================================

class TestPreFinalizeCheck:
    def setup_method(self):
        self.engine = ReflectionEngine()

    def test_empty_output_returns_critical(self):
        state = _make_state(task="write a function")
        state.final_output = ""
        note = self.engine.pre_finalize_check(state)
        assert note is not None
        assert note.severity == "critical"
        assert note.category == "incomplete"

    def test_good_output_returns_none(self):
        state = _make_state(task="write a function")
        state.final_output = "Here is the implementation of the function. It handles edge cases and passes all tests."
        note = self.engine.pre_finalize_check(state)
        assert note is None

    def test_planning_prefix_triggers_warning(self):
        state = _make_state(task="fix the bug")
        state.final_output = "I will now proceed to fix the bug by editing the file."
        note = self.engine.pre_finalize_check(state)
        assert note is not None
        assert "planning" in note.message.lower() or "forward" in note.message.lower()

    def test_very_short_output_triggers_info(self):
        state = _make_state(task="explain this function in detail")
        state.final_output = "Done."
        note = self.engine.pre_finalize_check(state)
        assert note is not None
        assert note.severity in ("info", "warning")


# ===========================================================================
# DegradedHandler
# ===========================================================================

class TestDegradedHandler:
    def setup_method(self):
        self.handler = DegradedHandler()

    def test_sets_degraded_status(self):
        state = _make_state()
        self.handler.handle(state, BudgetExceeded("over budget"))
        assert state.status == RunStatus.DEGRADED

    def test_no_tool_results_gives_empty_message(self):
        state = _make_state()
        state.final_output = None
        self.handler.handle(state, BudgetExceeded("budget"))
        assert "no tool results" in state.final_output or "Budget exhausted" in state.final_output

    def test_partial_results_included_in_output(self):
        state = _make_state()
        state.tool_results = [
            ToolResult(call_id="c1", tool_name="file_read", ok=True, output="found the bug at line 42"),
            ToolResult(call_id="c2", tool_name="run_tests", ok=False, output="", error="pytest failed"),
        ]
        self.handler.handle(state, BudgetExceeded("over limit"))
        assert state.status == RunStatus.DEGRADED
        assert "file_read" in state.final_output or "run_tests" in state.final_output

    def test_preserves_existing_final_output(self):
        state = _make_state()
        state.final_output = "Partial answer already written."
        self.handler.handle(state, BudgetExceeded("over limit"))
        assert "Partial answer already written." in state.final_output
        assert "Degraded" in state.final_output

    def test_truncates_long_tool_output(self):
        state = _make_state()
        long_output = "x" * 1000
        state.tool_results = [
            ToolResult(call_id="c1", tool_name="glob", ok=True, output=long_output)
        ]
        self.handler.handle(state, BudgetExceeded("over limit"))
        # Output should be truncated (not the full 1000 chars repeated)
        assert "truncated" in state.final_output or len(state.final_output) < 2000


# ===========================================================================
# PromptSelector
# ===========================================================================

class TestPromptSelector:
    def _make_selector(self):
        from packages.runtime.prompts import PromptSelector
        return PromptSelector(workspace_root="/workspace")

    def _base_datetime(self):
        return {"date": "2025-01-01", "datetime": "2025-01-01 12:00:00 CST", "timezone": "Asia/Shanghai"}

    def test_base_prompt_included(self):
        sel = self._make_selector()
        state = _make_state()
        prompt = sel.assemble(state, datetime_dict=self._base_datetime(), loaded_tools="file_read", dynamic_tools="")
        assert "agentic coding assistant" in prompt
        assert "Tool policy" in prompt

    def test_code_overlay_for_code_task_type(self):
        sel = self._make_selector()
        state = _make_state()
        state.metadata["task_type"] = "code"
        prompt = sel.assemble(state, datetime_dict=self._base_datetime(), loaded_tools="", dynamic_tools="")
        assert "Code Agent mode" in prompt
        assert "run_tests" in prompt

    def test_reviewer_overlay_for_review_task_type(self):
        sel = self._make_selector()
        state = _make_state()
        state.metadata["task_type"] = "review"
        prompt = sel.assemble(state, datetime_dict=self._base_datetime(), loaded_tools="", dynamic_tools="")
        assert "Code review mode" in prompt
        assert "five-phase" in prompt

    def test_planner_overlay_when_plan_mode_active(self):
        sel = self._make_selector()
        state = _make_state()
        state.metadata["plan_mode"] = {"active": True}
        prompt = sel.assemble(state, datetime_dict=self._base_datetime(), loaded_tools="", dynamic_tools="")
        assert "Plan mode is active" in prompt

    def test_react_overlay_for_agentic_mode(self):
        sel = self._make_selector()
        state = _make_state()
        state.metadata["autonomy_policy"] = {"mode": "agentic"}
        prompt = sel.assemble(state, datetime_dict=self._base_datetime(), loaded_tools="", dynamic_tools="")
        assert "ReAct" in prompt or "Thought:" in prompt

    def test_no_specialist_overlay_for_default(self):
        sel = self._make_selector()
        state = _make_state()
        prompt = sel.assemble(state, datetime_dict=self._base_datetime(), loaded_tools="", dynamic_tools="")
        # Default state should not include any specialist overlays
        assert "Code Agent mode" not in prompt
        assert "Code review mode" not in prompt
        assert "Plan mode is active" not in prompt

    def test_reflection_note_included_when_set(self):
        sel = self._make_selector()
        state = _make_state()
        state.metadata["_reflection"] = "[Reflection/loop] Detected repetitive calls.\nSuggestion: switch approach."
        prompt = sel.assemble(state, datetime_dict=self._base_datetime(), loaded_tools="", dynamic_tools="")
        assert "System Observation" in prompt
        assert "Detected repetitive" in prompt

    def test_recall_text_included(self):
        sel = self._make_selector()
        state = _make_state()
        state.metadata["recall_text"] = "Previously remembered: the bug is in utils.py"
        prompt = sel.assemble(state, datetime_dict=self._base_datetime(), loaded_tools="", dynamic_tools="")
        assert "Previously remembered" in prompt

    def test_autonomy_text_included(self):
        sel = self._make_selector()
        state = _make_state()
        prompt = sel.assemble(
            state,
            datetime_dict=self._base_datetime(),
            loaded_tools="",
            dynamic_tools="",
            autonomy_text="Autonomous workflow policy for this user message:\n- Mode: agentic",
        )
        assert "Autonomous workflow policy" in prompt

    def test_code_overlay_not_selected_when_plan_mode(self):
        """plan_mode takes priority over task_type=code"""
        sel = self._make_selector()
        state = _make_state()
        state.metadata["task_type"] = "code"
        state.metadata["plan_mode"] = {"active": True}
        prompt = sel.assemble(state, datetime_dict=self._base_datetime(), loaded_tools="", dynamic_tools="")
        assert "Plan mode is active" in prompt
        assert "Code Agent mode" not in prompt
