from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator


def now() -> float:
    """返回当前时间的高精度时间戳，单位为秒。"""
    return time.perf_counter()


def elapsed_ms(start: float) -> int:
    """计算从 start 到当前时间的经过时间，返回毫秒数。"""
    return int((time.perf_counter() - start) * 1000)


def record_timing(target: dict[str, Any], name: str, duration_ms: int, **details: Any) -> None:
    """记录时间信息到目标字典中，包括总计和各个命名的时间段。"""
    summary = target.setdefault("timing_summary", {})
    summary["total_recorded_ms"] = int(summary.get("total_recorded_ms", 0)) + duration_ms
    summary[name] = int(summary.get(name, 0)) + duration_ms

    entries = target.setdefault("timings", [])
    entry = {"name": name, "duration_ms": duration_ms}
    if details:
        entry.update(details)
    entries.append(entry)


@contextmanager
def timing(target: dict[str, Any], name: str, **details: Any) -> Iterator[None]:
    """上下文管理器，用于记录代码块的执行时间。"""
    start = now()
    try:
        yield
    finally:
        record_timing(target, name, elapsed_ms(start), **details)
