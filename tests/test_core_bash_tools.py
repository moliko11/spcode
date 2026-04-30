from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from packages.core_io.bash_tools import BashSessionManager, BashTool


def test_bash_tool_runs_simple_command(tmp_path: Path) -> None:
    tool = BashTool(BashSessionManager(workspace_root=tmp_path))

    result = asyncio.run(tool.arun({"command": "Write-Output 'hello'", "session_id": "s1"}))

    assert result["ok"] is True
    assert "hello" in result["stdout"]
    assert "__CODEX_CWD__" not in result["stdout"]
    assert result["exit_code"] == 0


def test_bash_tool_blocks_dangerous_command(tmp_path: Path) -> None:
    tool = BashTool(BashSessionManager(workspace_root=tmp_path))

    with pytest.raises(ValueError, match="blocked by pattern"):
        asyncio.run(tool.arun({"command": "Remove-Item test.txt"}))


def test_bash_session_tracks_cwd(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    tool = BashTool(BashSessionManager(workspace_root=tmp_path))

    first = asyncio.run(
        tool.arun({"command": "Write-Output 'one'", "session_id": "s1", "cwd": "sub"})
    )
    second = asyncio.run(tool.arun({"command": "Write-Output 'two'", "session_id": "s1"}))

    assert first["cwd"] == "sub"
    assert second["cwd"] == "sub"


def test_bash_tool_failure_exposes_error(tmp_path: Path) -> None:
    tool = BashTool(BashSessionManager(workspace_root=tmp_path))

    result = asyncio.run(tool.arun({"command": "Get-Item missing-file.txt", "session_id": "s1"}))

    assert result["ok"] is False
    assert result["error"]
    assert result["error"] != "tool execution failed"


def test_powershell_mode_accepts_double_ampersand(tmp_path: Path) -> None:
    tool = BashTool(BashSessionManager(workspace_root=tmp_path))

    result = asyncio.run(tool.arun({"command": "Write-Output 'one' && Write-Output 'two'"}))

    assert result["ok"] is True
    assert "one" in result["stdout"]
    assert "two" in result["stdout"]
