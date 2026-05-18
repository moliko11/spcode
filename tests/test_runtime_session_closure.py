from __future__ import annotations

import time

import pytest

from packages.runtime.agent_loop import AgentRuntime
from packages.runtime.events import EventBus
from packages.runtime.models import AgentState, RunStatus
from packages.runtime.store import FileCheckpointStore, FileSessionStore


@pytest.mark.asyncio
async def test_load_closed_session_messages_closes_trailing_user_turn(tmp_path) -> None:
    store = FileSessionStore(tmp_path / "sessions")
    store.root.mkdir(parents=True)
    await store.append_message("s1", "user", "帮我查高铁")
    await store.append_message("s1", "user", "hello")

    runtime = object.__new__(AgentRuntime)
    runtime.session_store = store

    messages = await runtime._load_closed_session_messages("s1")

    assert [message.role for message in messages] == ["user", "user", "assistant"]
    assert "中断" in messages[-1].content
    assert "独立请求" in messages[-1].content

    persisted = await store.load_messages("s1")
    assert [message.role for message in persisted] == ["user", "user", "assistant"]


@pytest.mark.asyncio
async def test_load_closed_session_messages_keeps_completed_turn(tmp_path) -> None:
    store = FileSessionStore(tmp_path / "sessions")
    store.root.mkdir(parents=True)
    await store.append_message("s1", "user", "hello")
    await store.append_message("s1", "assistant", "hi")

    runtime = object.__new__(AgentRuntime)
    runtime.session_store = store

    messages = await runtime._load_closed_session_messages("s1")

    assert [message.content for message in messages] == ["hello", "hi"]


@pytest.mark.asyncio
async def test_mark_failed_appends_assistant_failure_message(tmp_path) -> None:
    session_store = FileSessionStore(tmp_path / "sessions")
    checkpoint_store = FileCheckpointStore(tmp_path / "checkpoints")
    session_store.root.mkdir(parents=True)
    checkpoint_store.root.mkdir(parents=True)
    await session_store.append_message("s1", "user", "会失败的任务")

    runtime = object.__new__(AgentRuntime)
    runtime.session_store = session_store
    runtime.checkpoint_store = checkpoint_store
    runtime.event_bus = EventBus()

    state = AgentState(run_id="run-failed", user_id="u1", session_id="s1", task="会失败的任务")

    await runtime._mark_failed(state, RuntimeError("boom"), time.time())

    assert state.status == RunStatus.FAILED
    assert state.final_output is not None
    assert "执行失败" in state.final_output
    messages = await session_store.load_messages("s1")
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[-1].content == state.final_output
