from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from packages.core_io import FileEditTool, FileReadTool, FileWriteTool


def test_file_read_whole_file(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"
    path.write_text("a\nb\nc\n", encoding="utf-8")
    tool = FileReadTool(workspace_root=tmp_path)

    result = asyncio.run(tool.arun({"path": "demo.txt"}))

    assert result["path"] == "demo.txt"
    assert result["content"] == "a\nb\nc\n"
    assert result["line_count"] == 3
    assert result["truncated"] is False


def test_file_read_line_range(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"
    path.write_text("a\nb\nc\nd\n", encoding="utf-8")
    tool = FileReadTool(workspace_root=tmp_path)

    result = asyncio.run(tool.arun({"path": "demo.txt", "start_line": 2, "end_line": 3}))

    assert result["content"] == "b\nc"
    assert result["start_line"] == 2
    assert result["end_line"] == 3
    assert result["line_count"] == 2


def test_file_read_binary_rejected(tmp_path: Path) -> None:
    path = tmp_path / "demo.bin"
    path.write_bytes(b"\x00\x01\x02")
    tool = FileReadTool(workspace_root=tmp_path)

    with pytest.raises(ValueError, match="binary files are not supported"):
        asyncio.run(tool.arun({"path": "demo.bin"}))


def test_file_write_create_and_overwrite_modes(tmp_path: Path) -> None:
    tool = FileWriteTool(workspace_root=tmp_path)

    created = asyncio.run(tool.arun({"path": "hello.py", "content": "print(1)\n", "mode": "create"}))
    assert created["created"] is True
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "print(1)\n"

    overwritten = asyncio.run(
        tool.arun({"path": "hello.py", "content": "print(2)\n", "mode": "overwrite"})
    )
    assert overwritten["overwritten"] is True
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "print(2)\n"


def test_file_edit_replace_exact_success(tmp_path: Path) -> None:
    path = tmp_path / "hello.py"
    path.write_text("x = 1\nprint(x)\n", encoding="utf-8")
    tool = FileEditTool(workspace_root=tmp_path)

    result = asyncio.run(
        tool.arun(
            {
                "path": "hello.py",
                "action": "replace_exact",
                "old_str": "x = 1",
                "new_str": "x = 2",
                "expected_occurrences": 1,
            }
        )
    )

    assert result["changed"] is True
    assert "x = 2" in path.read_text(encoding="utf-8")


def test_file_edit_replace_exact_multiple_matches_fails(tmp_path: Path) -> None:
    path = tmp_path / "hello.py"
    path.write_text("x = 1\nx = 1\n", encoding="utf-8")
    tool = FileEditTool(workspace_root=tmp_path)

    with pytest.raises(ValueError, match="expected 1 occurrences but found 2"):
        asyncio.run(
            tool.arun(
                {
                    "path": "hello.py",
                    "action": "replace_exact",
                    "old_str": "x = 1",
                    "new_str": "x = 2",
                    "expected_occurrences": 1,
                }
            )
        )


def test_file_edit_insert_at_line_success(tmp_path: Path) -> None:
    path = tmp_path / "hello.py"
    path.write_text("line1\nline3\n", encoding="utf-8")
    tool = FileEditTool(workspace_root=tmp_path)

    result = asyncio.run(
        tool.arun(
            {
                "path": "hello.py",
                "action": "insert_at_line",
                "line_no": 2,
                "text": "line2",
            }
        )
    )

    assert result["changed"] is True
    assert path.read_text(encoding="utf-8") == "line1\nline2\nline3\n"
