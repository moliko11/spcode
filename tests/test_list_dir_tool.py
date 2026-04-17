from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest


class TestListDirTool:
    def test_list_empty_directory(self, tmp_path: Path) -> None:
        from examples.example4 import ListDirTool, WORKSPACE_DIR

        tool = ListDirTool()

        async def run_test() -> str:
            return await tool.arun({"path": "."})

        result = asyncio.run(run_test())
        assert result == ""

    def test_list_directory_with_files(self, tmp_path: Path) -> None:
        from examples.example4 import ListDirTool, WORKSPACE_DIR
        import examples.example4 as m

        original_workspace = m.WORKSPACE_DIR
        m.WORKSPACE_DIR = tmp_path
        try:
            (tmp_path / "file1.txt").write_text("hello")
            (tmp_path / "subdir").mkdir()

            tool = ListDirTool()

            async def run_test() -> str:
                return await tool.arun({"path": "."})

            result = asyncio.run(run_test())
            lines = result.split("\n")
            assert len(lines) == 2
            assert any("file1.txt" in line for line in lines)
            assert any("subdir" in line for line in lines)
        finally:
            m.WORKSPACE_DIR = original_workspace

    def test_list_nonexistent_directory(self) -> None:
        from examples.example4 import ListDirTool

        tool = ListDirTool()

        async def run_test():
            return await tool.arun({"path": "nonexistent_dir"})

        with pytest.raises(FileNotFoundError):
            asyncio.run(run_test())

    def test_list_file_instead_of_directory(self, tmp_path: Path) -> None:
        from examples.example4 import ListDirTool
        import examples.example4 as m

        original_workspace = m.WORKSPACE_DIR
        m.WORKSPACE_DIR = tmp_path
        try:
            (tmp_path / "notadir.txt").write_text("hello")

            tool = ListDirTool()

            async def run_test():
                return await tool.arun({"path": "notadir.txt"})

            with pytest.raises(NotADirectoryError):
                asyncio.run(run_test())
        finally:
            m.WORKSPACE_DIR = original_workspace

    def test_output_format(self, tmp_path: Path) -> None:
        from examples.example4 import ListDirTool
        import examples.example4 as m

        original_workspace = m.WORKSPACE_DIR
        m.WORKSPACE_DIR = tmp_path
        try:
            (tmp_path / "aaa.txt").write_text("a")
            (tmp_path / "bbb.txt").write_text("b")
            (tmp_path / "zzz_dir").mkdir()

            tool = ListDirTool()

            async def run_test() -> str:
                return await tool.arun({"path": "."})

            result = asyncio.run(run_test())
            lines = result.split("\n")

            assert lines[0].startswith("dir\t")
            assert lines[1].startswith("file\t")
            assert lines[2].startswith("file\t")
        finally:
            m.WORKSPACE_DIR = original_workspace
