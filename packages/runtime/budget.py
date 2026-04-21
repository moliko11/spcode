from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .config import WORKSPACE_DIR
from .models import AgentState, BudgetExceeded, ToolResult, to_jsonable


class RetryPolicy:
    """
    重试策略
    """
    def __init__(self, max_retries: int = 2, base_delay: float = 0.4) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.last_retry_count = 0

    async def run(
        self,
        func: Callable[[], Any],
        retryable: Callable[[Exception], bool] | None = None,
        max_retries: int | None = None,
    ) -> Any:
        last_error: Optional[Exception] = None
        retryable = retryable or (lambda exc: True)
        retries = self.max_retries if max_retries is None else max_retries
        self.last_retry_count = 0
        for attempt in range(retries + 1):
            try:
                self.last_retry_count = attempt
                return await func()
            except Exception as exc:
                last_error = exc
                if attempt >= retries or not retryable(exc):
                    break
                await asyncio.sleep(self.base_delay * (2**attempt))
        assert last_error is not None
        raise last_error


class IdempotencyStore:
    """
    幂等性存储
    """
    def __init__(self) -> None:
        self._results: dict[str, ToolResult] = {}

    def get(self, key: str) -> ToolResult | None:
        return self._results.get(key)

    def set(self, key: str, result: ToolResult) -> None:
        self._results[key] = result

    def export_snapshot(self) -> dict[str, Any]:
        return {key: to_jsonable(value) for key, value in self._results.items()}

    def load_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._results = {key: ToolResult(**value) for key, value in snapshot.items()}


class BudgetController:
    """
    预算控制器
    """
    def __init__(self, max_steps: int, max_tool_calls: int, max_seconds: int) -> None:
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.max_seconds = max_seconds

    def check(self, state: AgentState) -> None:
        if state.step >= self.max_steps:
            raise BudgetExceeded(f"max steps exceeded: {self.max_steps}")
        if len(state.tool_results) >= self.max_tool_calls:
            raise BudgetExceeded(f"max tool calls exceeded: {self.max_tool_calls}")
        if time.time() - state.started_at >= self.max_seconds:
            raise BudgetExceeded(f"max runtime exceeded: {self.max_seconds}s")


def snapshot_workspace(root: Path = WORKSPACE_DIR) -> dict[str, tuple[int, int]]:
    """
    工作空间快照
    """
    snapshot: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return snapshot
    for path in root.rglob("*"):
        if path.is_file():
            stat = path.stat()
            snapshot[str(path.relative_to(root))] = (int(stat.st_mtime_ns), stat.st_size)
    return snapshot


def diff_workspace(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    """
    工作空间差异
    """
    changed = set(before) ^ set(after)
    for path, stat in before.items():
        if path in after and after[path] != stat:
            changed.add(path)
    return sorted(changed)
