from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator


def now() -> float:
    return time.perf_counter()


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def record_timing(target: dict[str, Any], name: str, duration_ms: int, **details: Any) -> None:
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
    start = now()
    try:
        yield
    finally:
        record_timing(target, name, elapsed_ms(start), **details)
