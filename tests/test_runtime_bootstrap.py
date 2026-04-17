from __future__ import annotations

import asyncio

from packages.runtime.bootstrap import build_runtime


def test_build_runtime_smoke() -> None:
    runtime = build_runtime()
    assert runtime.registry.get_spec("tool_search").name == "tool_search"
    assert runtime.registry.get_spec("file_read").name == "file_read"
    assert runtime.session_store is not None
    assert runtime.memory_manager is None


def test_session_store_empty_load() -> None:
    runtime = build_runtime()
    messages = asyncio.run(runtime.session_store.load_messages("runtime_bootstrap_missing"))
    assert messages == []
