from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from packages.memory.models import MemoryEntry, MemoryType
from packages.memory.store import FileMemoryStore


def _make_entry(user_id: str = "u1", memory_type: MemoryType = MemoryType.EPISODE, content: str = "test content") -> MemoryEntry:
    return MemoryEntry(
        memory_id="test-id-001",
        user_id=user_id,
        memory_type=memory_type,
        content=content,
        summary="test summary",
        tags=["tag1", "tag2"],
        importance=0.6,
        created_at=1000.0,
        source="run_summary",
    )


@pytest.fixture()
def tmp_store(tmp_path: Path) -> FileMemoryStore:
    return FileMemoryStore(tmp_path)


def test_save_and_list_recent(tmp_store: FileMemoryStore) -> None:
    entry = _make_entry()
    asyncio.run(tmp_store.save(entry))
    results = asyncio.run(tmp_store.list_recent("u1", limit=10))
    assert len(results) == 1
    assert results[0].memory_id == "test-id-001"
    assert results[0].content == "test content"
    assert results[0].memory_type == MemoryType.EPISODE


def test_list_recent_empty(tmp_store: FileMemoryStore) -> None:
    results = asyncio.run(tmp_store.list_recent("no_such_user"))
    assert results == []


def test_list_recent_limit(tmp_store: FileMemoryStore) -> None:
    import uuid

    for i in range(5):
        e = MemoryEntry(
            memory_id=str(uuid.uuid4()),
            user_id="u1",
            memory_type=MemoryType.EPISODE,
            content=f"entry {i}",
            summary=f"summary {i}",
            tags=[],
            importance=0.5,
            created_at=float(i),
            source="run_summary",
        )
        asyncio.run(tmp_store.save(e))

    results = asyncio.run(tmp_store.list_recent("u1", limit=3))
    assert len(results) == 3
    # 应按 created_at 降序返回最新 3 条
    assert results[0].created_at >= results[1].created_at


def test_search_returns_relevant(tmp_store: FileMemoryStore) -> None:
    entry = _make_entry(content="python async agent loop implementation")
    asyncio.run(tmp_store.save(entry))

    results = asyncio.run(tmp_store.search("async agent", "u1", limit=5))
    assert len(results) >= 1
    assert results[0].memory_id == "test-id-001"


def test_search_no_match(tmp_store: FileMemoryStore) -> None:
    entry = _make_entry(content="python async agent loop implementation")
    asyncio.run(tmp_store.save(entry))

    results = asyncio.run(tmp_store.search("completely unrelated xyz123", "u1", limit=5))
    # 无匹配时应返回空列表或低分项，但不报错
    assert isinstance(results, list)


def test_update_access(tmp_store: FileMemoryStore) -> None:
    entry = _make_entry()
    asyncio.run(tmp_store.save(entry))
    asyncio.run(tmp_store.update_access("test-id-001", "u1"))

    results = asyncio.run(tmp_store.list_recent("u1"))
    assert results[0].access_count == 1


def test_delete(tmp_store: FileMemoryStore) -> None:
    entry = _make_entry()
    asyncio.run(tmp_store.save(entry))
    asyncio.run(tmp_store.delete("test-id-001", "u1"))

    results = asyncio.run(tmp_store.list_recent("u1"))
    assert results == []
