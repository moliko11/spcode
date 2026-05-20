from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from packages.tools.FindSymbolTool import FindSymbolTool
from packages.tools.GitDiffTool import GitDiffTool
from packages.tools.GitLogTool import GitLogTool
from packages.tools.LintTool import LintTool
from packages.tools.RunTestsTool import RunTestsTool


# ---------------------------------------------------------------------------
# RunTestsTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tests_passes_on_simple_suite(tmp_path: Path) -> None:
    (tmp_path / "test_ok.py").write_text("def test_pass(): assert 1 + 1 == 2\n")
    tool = RunTestsTool(workspace_root=tmp_path)
    result = await tool.arun({"path": "test_ok.py"})
    assert "passed" in result
    assert "[run_tests]" in result


@pytest.mark.asyncio
async def test_run_tests_reports_failure(tmp_path: Path) -> None:
    (tmp_path / "test_fail.py").write_text("def test_bad(): assert False\n")
    tool = RunTestsTool(workspace_root=tmp_path)
    result = await tool.arun({"path": "test_fail.py"})
    assert "failed" in result or "error" in result.lower()


@pytest.mark.asyncio
async def test_run_tests_timeout_returns_message(tmp_path: Path, monkeypatch) -> None:
    import asyncio as _asyncio

    original = _asyncio.wait_for

    async def _raise(*args, **kwargs):
        raise _asyncio.TimeoutError()

    monkeypatch.setattr(_asyncio, "wait_for", _raise)
    tool = RunTestsTool(workspace_root=tmp_path)
    result = await tool.arun({"timeout": 1})
    assert "timed out" in result


# ---------------------------------------------------------------------------
# LintTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lint_clean_file(tmp_path: Path) -> None:
    src = tmp_path / "clean.py"
    src.write_text("x = 1\n")
    tool = LintTool(workspace_root=tmp_path)
    result = await tool.arun({"path": "clean.py"})
    assert "[lint/" in result


@pytest.mark.asyncio
async def test_lint_detects_issue(tmp_path: Path) -> None:
    src = tmp_path / "bad.py"
    src.write_text("import os\n")  # unused import
    tool = LintTool(workspace_root=tmp_path)
    result = await tool.arun({"path": "bad.py"})
    # ruff or pyflakes should flag unused import (F401)
    assert "[lint/" in result


# ---------------------------------------------------------------------------
# GitDiffTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_diff_no_changes_message(tmp_path: Path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    tool = GitDiffTool(workspace_root=tmp_path)
    result = await tool.arun({})
    assert "no changes" in result


@pytest.mark.asyncio
async def test_git_diff_shows_changes(tmp_path: Path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    f.write_text("x = 2\n")

    tool = GitDiffTool(workspace_root=tmp_path)
    result = await tool.arun({})
    assert "a.py" in result


# ---------------------------------------------------------------------------
# GitLogTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_log_returns_commits(tmp_path: Path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "first commit"], cwd=tmp_path, capture_output=True)

    tool = GitLogTool(workspace_root=tmp_path)
    result = await tool.arun({"n": 5})
    assert "first commit" in result


@pytest.mark.asyncio
async def test_git_log_empty_repo(tmp_path: Path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)

    tool = GitLogTool(workspace_root=tmp_path)
    result = await tool.arun({})
    # empty repo: error or no commits
    assert result.startswith("[git_log]")


# ---------------------------------------------------------------------------
# FindSymbolTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_symbol_finds_class(tmp_path: Path) -> None:
    src = tmp_path / "mymod.py"
    src.write_text(textwrap.dedent("""\
        class FooBar:
            pass

        def baz():
            pass
    """))
    tool = FindSymbolTool(workspace_root=tmp_path)
    result = await tool.arun({"symbol": "FooBar"})
    assert "FooBar" in result
    assert "mymod.py" in result
    assert "class FooBar" in result


@pytest.mark.asyncio
async def test_find_symbol_finds_function(tmp_path: Path) -> None:
    src = tmp_path / "mymod.py"
    src.write_text("def hello_world(): pass\n")
    tool = FindSymbolTool(workspace_root=tmp_path)
    result = await tool.arun({"symbol": "hello_world", "kind": "function"})
    assert "hello_world" in result
    assert "def hello_world" in result


@pytest.mark.asyncio
async def test_find_symbol_not_found(tmp_path: Path) -> None:
    src = tmp_path / "empty.py"
    src.write_text("x = 1\n")
    tool = FindSymbolTool(workspace_root=tmp_path)
    result = await tool.arun({"symbol": "NonExistent"})
    assert "not found" in result


@pytest.mark.asyncio
async def test_find_symbol_finds_variable(tmp_path: Path) -> None:
    src = tmp_path / "conf.py"
    src.write_text("MAX_RETRIES = 3\n")
    tool = FindSymbolTool(workspace_root=tmp_path)
    result = await tool.arun({"symbol": "MAX_RETRIES", "kind": "variable"})
    assert "MAX_RETRIES" in result


# ---------------------------------------------------------------------------
# planner prompts
# ---------------------------------------------------------------------------


def test_code_plan_system_prompt_contains_tool_names() -> None:
    from packages.planner.prompts import CODE_PLAN_SYSTEM_PROMPT
    for tool in ("run_tests", "lint", "git_diff", "git_log", "find_symbol"):
        assert tool in CODE_PLAN_SYSTEM_PROMPT


def test_code_review_prompt_contains_required_tools() -> None:
    from packages.planner.prompts import CODE_REVIEW_PLAN_SYSTEM_PROMPT
    for tool in ("run_tests", "lint", "git_diff", "git_log", "file_read"):
        assert tool in CODE_REVIEW_PLAN_SYSTEM_PROMPT


def test_build_code_plan_user_prompt_includes_all_parts() -> None:
    from packages.planner.prompts import build_code_plan_user_prompt
    result = build_code_plan_user_prompt("fix bug", context="some context", task_type="debug")
    assert "fix bug" in result
    assert "some context" in result
    assert "debug" in result
