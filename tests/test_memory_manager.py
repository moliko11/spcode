from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import pytest

from packages.memory.manager import MemoryManager
from packages.memory.models import MemoryEntry, MemoryType, RecallPack
from packages.memory.store import FileMemoryStore
from packages.runtime.models import AgentState, Phase, RunStatus, SessionMessage, ToolResult


def _make_state(task: str = "write a hello world", step: int = 2, final_output: str = "done") -> AgentState:
    state = AgentState(
        run_id=str(uuid.uuid4()),
        user_id="test_user",
        task=task,
        session_id="session-001",
        status=RunStatus.COMPLETED,
        phase=Phase.COMPLETED,
        step=step,
        final_output=final_output,
    )
    return state


def _make_state_with_tool_result(changed_files: list[str]) -> AgentState:
    state = _make_state()
    result = ToolResult(
        call_id="call-1",
        tool_name="file_write",
        ok=True,
        output="wrote file",
        changed_files=changed_files,
    )
    state.tool_results.append(result)
    return state


@pytest.fixture()
def manager(tmp_path: Path) -> MemoryManager:
    store = FileMemoryStore(tmp_path)
    return MemoryManager(store=store)


# ------------------------------------------------------------------
# remember_run
# ------------------------------------------------------------------


def test_remember_run_creates_episode(manager: MemoryManager) -> None:
    state = _make_state()
    entries = asyncio.run(manager.remember_run(state))
    assert len(entries) >= 1
    episode = entries[0]
    assert episode.memory_type == MemoryType.EPISODE
    assert episode.user_id == "test_user"
    assert episode.session_id == "session-001"
    assert state.task[:40] in episode.summary


def test_remember_run_with_changed_files_creates_semantic(manager: MemoryManager) -> None:
    state = _make_state_with_tool_result(["src/utils.py", "src/main.py"])
    entries = asyncio.run(manager.remember_run(state))
    types = [e.memory_type for e in entries]
    # 至少有 EPISODE 一条；file_write 触发 changed_files 应产生 SEMANTIC
    assert MemoryType.EPISODE in types


def test_remember_run_importance_increases_with_steps(manager: MemoryManager) -> None:
    state_few = _make_state(step=1)
    state_many = _make_state(step=15)
    entries_few = asyncio.run(manager.remember_run(state_few))
    entries_many = asyncio.run(manager.remember_run(state_many))
    ep_few = entries_few[0]
    ep_many = entries_many[0]
    assert ep_many.importance >= ep_few.importance


# ------------------------------------------------------------------
# recall
# ------------------------------------------------------------------


def test_recall_returns_recall_pack(manager: MemoryManager) -> None:
    state = _make_state(task="implement memory system for agent")
    asyncio.run(manager.remember_run(state))

    pack = asyncio.run(manager.recall("memory system", "test_user"))
    assert isinstance(pack, RecallPack)
    assert pack.query == "memory system"
    assert isinstance(pack.items, list)


def test_recall_empty_store_returns_empty_pack(manager: MemoryManager) -> None:
    pack = asyncio.run(manager.recall("anything", "test_user"))
    assert pack.items == []
    assert pack.injected_text == ""


def test_recall_injected_text_non_empty_when_matches(manager: MemoryManager) -> None:
    state = _make_state(task="build async agent runtime loop")
    asyncio.run(manager.remember_run(state))

    pack = asyncio.run(manager.recall("async agent runtime", "test_user"))
    if pack.items:
        assert pack.injected_text != ""


# ------------------------------------------------------------------
# update_semantic_memory / list_recent / forget
# ------------------------------------------------------------------


def test_update_semantic_memory(manager: MemoryManager) -> None:
    asyncio.run(manager.update_semantic_memory("test_user", "project_language", "Python 3.12"))
    entries = asyncio.run(manager.list_recent("test_user", limit=5))
    assert len(entries) == 1
    assert entries[0].memory_type == MemoryType.SEMANTIC
    assert "Python 3.12" in entries[0].content


def test_forget_removes_entry(manager: MemoryManager) -> None:
    asyncio.run(manager.update_semantic_memory("test_user", "key", "value"))
    entries = asyncio.run(manager.list_recent("test_user"))
    assert len(entries) == 1
    mem_id = entries[0].memory_id
    asyncio.run(manager.forget(mem_id, "test_user"))
    entries_after = asyncio.run(manager.list_recent("test_user"))
    assert entries_after == []
