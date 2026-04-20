"""
tests/test_phase_c_recall_injection.py
────────────────────────────────────────
Phase C 集成验证：recall_text 被正确注入 system prompt。
不依赖真实 LLM，只测试 MessageBuilder + MemoryManager + AgentState 的协作。
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from packages.memory.manager import MemoryManager
from packages.memory.store import FileMemoryStore
from packages.runtime.message_builder import MessageBuilder
from packages.runtime.models import AgentState, Phase, RunStatus, SessionMessage


def _make_state_with_recall(recall_text: str | None, task: str = "test task") -> AgentState:
    state = AgentState(
        run_id=str(uuid.uuid4()),
        user_id="user1",
        task=task,
        session_id="sess-c",
        status=RunStatus.RUNNING,
        phase=Phase.DECIDING,
        metadata={
            "tool_ledger": {},
            "loaded_tools": ["file_read"],
            "recall_text": recall_text,
        },
    )
    return state


# ── MessageBuilder 注入逻辑 ───────────────────────────────────────────────────

def test_system_prompt_contains_recall_text() -> None:
    """有 recall_text 时，system prompt 应包含它。"""
    recall = "Relevant memory:\n- Project fact: main entry is packages/runtime/bootstrap.py"
    state = _make_state_with_recall(recall)
    mb = MessageBuilder()
    prompt = mb.build_system_prompt(state)
    assert recall in prompt


def test_system_prompt_no_recall_section_when_empty() -> None:
    """recall_text 为空字符串或 None 时，不应出现 'Relevant memory' 字样。"""
    for recall in (None, ""):
        state = _make_state_with_recall(recall)
        mb = MessageBuilder()
        prompt = mb.build_system_prompt(state)
        assert "Relevant memory" not in prompt


def test_system_prompt_recall_appended_at_end() -> None:
    """recall_text 应追加在 prompt 末尾，不打断核心指令块。"""
    recall = "Relevant memory:\n- Recent run: implemented memory system"
    state = _make_state_with_recall(recall)
    mb = MessageBuilder()
    prompt = mb.build_system_prompt(state)
    # 核心指令在前
    assert prompt.index("You are an agentic") < prompt.index("Relevant memory")


def test_build_initial_messages_system_has_recall() -> None:
    """build_initial_messages 第一条消息（SystemMessage）content 中包含 recall_text。"""
    from langchain_core.messages import SystemMessage

    recall = "Relevant memory:\n- Project fact: runtime uses asyncio"
    state = _make_state_with_recall(recall)
    mb = MessageBuilder()
    msgs = mb.build_initial_messages(state)
    assert isinstance(msgs[0], SystemMessage)
    assert recall in msgs[0].content


# ── MemoryManager.recall → recall_text 链路 ───────────────────────────────────

@pytest.fixture()
def manager_with_memory(tmp_path: Path) -> MemoryManager:
    store = FileMemoryStore(tmp_path)
    manager = MemoryManager(store=store, workspace_id="/project/alpha")
    # 预存一条记忆
    asyncio.run(manager.update_semantic_memory(
        "user1",
        "entry_point",
        "packages/runtime/bootstrap.py is the runtime entry",
    ))
    return manager


def test_recall_produces_non_empty_injected_text(manager_with_memory: MemoryManager) -> None:
    """recall 命中记忆后 injected_text 不为空。"""
    pack = asyncio.run(manager_with_memory.recall("runtime bootstrap entry", "user1"))
    assert pack.items
    assert pack.injected_text != ""
    assert "Relevant memory" in pack.injected_text


def test_recall_text_flows_into_system_prompt(manager_with_memory: MemoryManager) -> None:
    """recall_text 写入 metadata 后，MessageBuilder 构建的 prompt 包含它。"""
    pack = asyncio.run(manager_with_memory.recall("runtime bootstrap entry", "user1"))
    state = _make_state_with_recall(pack.injected_text)
    prompt = MessageBuilder().build_system_prompt(state)
    assert "Relevant memory" in prompt


def test_no_cross_workspace_recall(tmp_path: Path) -> None:
    """不同 workspace_id 的记忆不会出现在 recall 结果里。"""
    store = FileMemoryStore(tmp_path)
    manager_a = MemoryManager(store=store, workspace_id="/project/alpha")
    manager_b = MemoryManager(store=store, workspace_id="/project/beta")

    asyncio.run(manager_a.update_semantic_memory("user1", "alpha_key", "alpha specific fact"))

    pack = asyncio.run(manager_b.recall("alpha_key alpha specific", "user1"))
    # manager_b 不应看到 alpha 的 workspace 专属记忆
    assert all(
        e.workspace_id != "/project/alpha"
        for e in pack.items
    )


def test_cross_project_global_memory_visible(tmp_path: Path) -> None:
    """workspace_id=None 的通用记忆对任何项目都可见。"""
    from packages.memory.models import MemoryEntry, MemoryType
    import time

    store = FileMemoryStore(tmp_path)
    # 手动写入一条 workspace_id=None 的通用记忆
    global_entry = MemoryEntry(
        memory_id=str(uuid.uuid4()),
        user_id="user1",
        workspace_id=None,
        memory_type=MemoryType.SEMANTIC,
        content="global convention: always use uv for dependency management",
        summary="global convention: always use uv",
        tags=["uv", "convention"],
        importance=0.8,
        created_at=time.time(),
        source="user_feedback",
    )
    asyncio.run(store.save(global_entry))

    manager_any = MemoryManager(store=store, workspace_id="/project/any")
    pack = asyncio.run(manager_any.recall("uv dependency management", "user1"))
    assert any("uv" in e.content for e in pack.items)
