from __future__ import annotations

import asyncio
from pathlib import Path

from packages.core_io import GlobTool, GrepTool


def test_glob_finds_python_files_and_ignores_cache(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.py").write_text("ignore\n", encoding="utf-8")
    tool = GlobTool(workspace_root=tmp_path)

    result = asyncio.run(tool.arun({"pattern": "**/*.py"}))

    assert result["matches"] == ["a.py"]


def test_grep_python_fallback_finds_matches(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("class ToolSpec:\n    pass\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nothing here\n", encoding="utf-8")
    tool = GrepTool(workspace_root=tmp_path)

    result = tool._run_python({"pattern": "ToolSpec", "base_path": ".", "is_regex": False, "max_matches": 20})

    assert result["match_count"] == 1
    assert result["matches"][0]["path"] == "a.py"
    assert result["matches"][0]["line_no"] == 1


def test_grep_file_glob_filters_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    tool = GrepTool(workspace_root=tmp_path)

    result = tool._run_python(
        {
            "pattern": "needle",
            "base_path": ".",
            "is_regex": False,
            "file_glob": "*.py",
            "max_matches": 20,
        }
    )

    assert result["match_count"] == 1
    assert result["matches"][0]["path"] == "a.py"


def test_grep_rg_json_parser_handles_windows_paths(tmp_path: Path) -> None:
    tool = GrepTool(workspace_root=tmp_path)
    target = tmp_path / "dir" / "a.txt"
    target.parent.mkdir()
    target.write_text("needle\n", encoding="utf-8")
    escaped = str(target).replace("\\", "\\\\")
    payload = (
        '{"type":"match","data":{"path":{"text":"'
        + escaped
        + '"},"lines":{"text":"needle\\n"},"line_number":1}}'
    )

    matches = tool._parse_rg_json(payload, max_matches=20)

    assert matches == [{"path": "dir/a.txt", "line_no": 1, "line_text": "needle"}]
