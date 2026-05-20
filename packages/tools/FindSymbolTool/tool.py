from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


class FindSymbolTool:
    """
    基于 Python AST 在工作区内查找类、函数或变量的定义位置。
    
    参数：
      - symbol: 要查找的符号名称（类名/函数名/变量名）
      - path: 限定搜索路径（相对于 workspace_root，可选，默认递归搜索所有 .py）
      - kind: 限定类型 "class"/"function"/"variable"/"any"（可选，默认 "any"）
      - max_results: 最多返回条数（可选，默认 30）
    """

    def __init__(self, workspace_root: str | Path = ".") -> None:
        self.workspace_root = Path(workspace_root).resolve()

    async def arun(self, arguments: dict[str, Any]) -> str:
        symbol = str(arguments.get("symbol", "")).strip()
        if not symbol:
            return "[find_symbol] error: 'symbol' is required"
        rel_path = arguments.get("path", "")
        kind = str(arguments.get("kind", "any")).lower()
        max_results = int(arguments.get("max_results", 30))

        search_root = self.workspace_root
        if rel_path:
            candidate = (self.workspace_root / rel_path).resolve()
            if candidate.is_dir() or candidate.is_file():
                search_root = candidate

        py_files = self._collect_py_files(search_root)
        results: list[str] = []
        for py_file in py_files:
            try:
                src = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src, filename=str(py_file))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                match = self._match(node, symbol, kind)
                if match:
                    try:
                        rel = py_file.relative_to(self.workspace_root)
                    except ValueError:
                        rel = py_file
                    results.append(f"{rel}:{node.lineno}  {match}")
                    if len(results) >= max_results:
                        break
            if len(results) >= max_results:
                break

        if not results:
            return f"[find_symbol] '{symbol}' not found"
        header = f"[find_symbol] '{symbol}' — {len(results)} result(s)"
        return header + "\n" + "\n".join(results)

    def _collect_py_files(self, root: Path) -> list[Path]:
        if root.is_file() and root.suffix == ".py":
            return [root]
        skip = {".venv", "__pycache__", ".git", "node_modules", ".mypy_cache"}
        files: list[Path] = []
        for p in root.rglob("*.py"):
            if any(part in skip for part in p.parts):
                continue
            files.append(p)
        return sorted(files)

    def _match(self, node: ast.AST, symbol: str, kind: str) -> str | None:
        if isinstance(node, ast.ClassDef) and node.name == symbol:
            if kind in ("any", "class"):
                return f"class {symbol}"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            if kind in ("any", "function"):
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                return f"{prefix} {symbol}"
        elif isinstance(node, ast.Assign) and kind in ("any", "variable"):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == symbol:
                    return f"{symbol} = ..."
        elif isinstance(node, (ast.AnnAssign,)) and kind in ("any", "variable"):
            if isinstance(node.target, ast.Name) and node.target.id == symbol:
                return f"{symbol}: ..."
        return None
