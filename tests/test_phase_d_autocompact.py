"""
tests/test_phase_d_autocompact.py

Phase D integration tests: Level 4 autocompact (LLM summary + transcript archive).
All LLM calls use MockSummarizer; no real network required.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from packages.memory.compaction import (
    AUTOCOMPACT_KEEP_ROUNDS,
    AUTOCOMPACT_THRESHOLD,
    CompactionPipeline,
    autocompact,
    estimate_messages_tokens,
)
from packages.memory.summarizer import TranscriptSummarizer, _format_messages
from packages.runtime.models import AgentState, Phase, RunStatus


# ── helpers ───────────────────────────────────────────────────────────────────

class MockSummarizer:
    """No-op summarizer that records calls."""

    def __init__(self, return_text: str = "MOCK SUMMARY") -> None:
        self._text = return_text
        self.calls: list[tuple[list, str]] = []

    async def summarize(self, messages: list[dict], task: str = "") -> str:
        self.calls.append((messages, task))
        return self._text


def _make_state(messages: list[dict], task: str = "test task", step: int = 10) -> AgentState:
    state = AgentState(
        run_id=str(uuid.uuid4()),
        user_id="u1",
        task=task,
        session_id="s1",
        status=RunStatus.RUNNING,
        phase=Phase.DECIDING,
        step=step,
        metadata={"tool_ledger": {}},
    )
    state.runtime_messages = messages
    return state


def _system(content: str = "system") -> dict:
    return {"type": "system", "content": content}


def _human(content: str = "hi") -> dict:
    return {"type": "human", "content": content}


def _ai_text(content: str = "ok") -> dict:
    return {"type": "ai", "content": content}


def _ai_tool(tool_name: str = "file_read", call_id: str = "tc1") -> dict:
    return {
        "type": "ai",
        "content": "",
        "tool_calls": [{"id": call_id, "name": tool_name, "arguments": {}}],
    }


def _tool_result(call_id: str = "tc1", content: str = "result") -> dict:
    return {"type": "tool", "tool_call_id": call_id, "content": content}


def _big_messages(n_rounds: int = 15, round_size: int = 20_000) -> list[dict]:
    """Build messages that exceed AUTOCOMPACT_THRESHOLD (50k tokens).

    estimate_tokens = len(text) // 4
    15 rounds x (20000 chars // 4) = 75,000 tokens > 50,000
    """
    msgs: list[dict] = [_system()]
    for i in range(n_rounds):
        cid = f"tc{i}"
        msgs.append(_ai_tool("file_read", cid))
        msgs.append(_tool_result(cid, "A" * round_size))
    return msgs


# ── TranscriptSummarizer unit tests ──────────────────────────────────────────

def test_format_messages_skips_system() -> None:
    msgs = [_system("SYSTEM"), _human("hello"), _ai_text("world")]
    result = _format_messages(msgs, task="test")
    assert "SYSTEM" not in result
    assert "hello" in result
    assert "world" in result


def test_format_messages_shows_tool_calls() -> None:
    msgs = [_ai_tool("grep", "tc1"), _tool_result("tc1", "found something")]
    result = _format_messages(msgs)
    assert "grep" in result
    assert "found something" in result


def test_mock_summarizer_returns_text() -> None:
    ms = MockSummarizer("summary text")
    result = asyncio.run(ms.summarize([_human("test")], task="my task"))
    assert result == "summary text"
    assert len(ms.calls) == 1
    assert ms.calls[0][1] == "my task"


def test_summarizer_empty_messages() -> None:
    """Only system messages -> _format_messages returns empty -> fallback placeholder."""

    class _AlwaysEmpty:
        async def ainvoke(self, _prompt: str):
            class R:
                content = "(no content to summarize)"
            return R()

    s = TranscriptSummarizer(llm=_AlwaysEmpty())
    result = asyncio.run(s.summarize([_system("ignored")]))
    assert "no content" in result


def test_summarizer_llm_failure() -> None:
    """LLM raising an exception should not propagate; returns failure placeholder."""

    class _BrokenLLM:
        async def ainvoke(self, _prompt: str):
            raise RuntimeError("network error")

    s = TranscriptSummarizer(llm=_BrokenLLM())
    result = asyncio.run(s.summarize([_human("test")]))
    assert "summarization failed" in result


# ── autocompact() function tests ──────────────────────────────────────────────

def test_autocompact_not_triggered_below_threshold() -> None:
    msgs = [_system(), _human("hi"), _ai_text("ok")]
    state = _make_state(msgs)
    assert estimate_messages_tokens(msgs) < AUTOCOMPACT_THRESHOLD
    ms = MockSummarizer()
    saved = asyncio.run(autocompact(state, ms))
    assert saved == 0
    assert len(ms.calls) == 0
    assert state.runtime_messages == msgs


def test_autocompact_triggered_above_threshold(tmp_path: Path) -> None:
    msgs = _big_messages()
    assert estimate_messages_tokens(msgs) > AUTOCOMPACT_THRESHOLD
    state = _make_state(msgs)
    ms = MockSummarizer("SUMMARY OF OLD WORK")
    saved = asyncio.run(autocompact(state, ms, archive_dir=tmp_path))

    assert saved > 0
    assert len(ms.calls) == 1
    contents = [m.get("content", "") for m in state.runtime_messages]
    assert any("SUMMARY OF OLD WORK" in c for c in contents)


def test_autocompact_keeps_recent_rounds(tmp_path: Path) -> None:
    msgs = _big_messages()
    state = _make_state(msgs)
    ms = MockSummarizer()
    asyncio.run(autocompact(state, ms, archive_dir=tmp_path))

    remaining_tool_calls = [
        m for m in state.runtime_messages
        if m.get("type") == "ai" and m.get("tool_calls")
    ]
    assert len(remaining_tool_calls) == AUTOCOMPACT_KEEP_ROUNDS


def test_autocompact_preserves_system_messages(tmp_path: Path) -> None:
    msgs = _big_messages()
    state = _make_state(msgs)
    ms = MockSummarizer()
    asyncio.run(autocompact(state, ms, archive_dir=tmp_path))

    system_msgs = [m for m in state.runtime_messages if m.get("type") == "system"]
    assert len(system_msgs) >= 2
    assert any("Autocompacted" in m.get("content", "") for m in system_msgs)


def test_autocompact_archives_to_jsonl(tmp_path: Path) -> None:
    msgs = _big_messages()
    state = _make_state(msgs)
    ms = MockSummarizer()
    asyncio.run(autocompact(state, ms, archive_dir=tmp_path))

    jsonl_files = list(tmp_path.glob(f"{state.run_id}_*.jsonl"))
    assert len(jsonl_files) == 1
    lines = jsonl_files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) > 0
    for line in lines:
        json.loads(line)  # each line must be valid JSON


def test_autocompact_no_archive_dir(tmp_path: Path) -> None:
    msgs = _big_messages()
    state = _make_state(msgs)
    ms = MockSummarizer("no archive summary")
    asyncio.run(autocompact(state, ms, archive_dir=None))

    assert len(ms.calls) == 1
    assert not list(tmp_path.glob("*.jsonl"))  # no files written


# ── CompactionPipeline Level 4 integration ───────────────────────────────────

def test_pipeline_prepare_no_summarizer() -> None:
    """Without summarizer, autocompact stats remain 0 even above threshold."""
    msgs = _big_messages()
    state = _make_state(msgs)
    pipeline = CompactionPipeline()  # no summarizer
    asyncio.run(pipeline.prepare(state))
    stats = state.metadata.get("compaction_stats", {})
    assert stats.get("auto_deleted_tokens", 0) == 0


def test_pipeline_prepare_triggers_autocompact(tmp_path: Path) -> None:
    """With summarizer + big messages, prepare() triggers Level 4."""
    msgs = _big_messages()
    state = _make_state(msgs)
    ms = MockSummarizer()
    pipeline = CompactionPipeline(summarizer=ms, archive_dir=tmp_path)
    asyncio.run(pipeline.prepare(state))
    stats = state.metadata.get("compaction_stats", {})
    assert stats.get("auto_deleted_tokens", 0) > 0


def test_pipeline_prepare_auto_not_triggered_small() -> None:
    msgs = [_system(), _human("task"), _ai_text("done")]
    state = _make_state(msgs)
    ms = MockSummarizer()
    pipeline = CompactionPipeline(summarizer=ms)
    asyncio.run(pipeline.prepare(state))
    stats = state.metadata.get("compaction_stats", {})
    assert stats.get("auto_deleted_tokens", 0) == 0
    assert len(ms.calls) == 0
