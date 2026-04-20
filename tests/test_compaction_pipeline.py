"""
tests/test_compaction_pipeline.py
──────────────────────────────────
Phase B: 压缩流水线单元测试。
"""
from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy

import pytest

from packages.memory.compaction import (
    MICRO_CONTENT_THRESHOLD,
    MICRO_KEEP_ROUNDS,
    SNIP_TOOL_ROUNDS,
    CompactionPipeline,
    CompactionStats,
    ContextProjector,
    estimate_messages_tokens,
    estimate_tokens,
    microcompact,
    snip_compact,
)
from packages.runtime.models import AgentState, Phase, RunStatus


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_state(messages: list[dict] | None = None) -> AgentState:
    state = AgentState(
        run_id=str(uuid.uuid4()),
        user_id="test_user",
        task="test task",
        session_id="sess-001",
        status=RunStatus.RUNNING,
        phase=Phase.DECIDING,
    )
    state.runtime_messages = messages or []
    return state


def _system() -> dict:
    return {"type": "system", "content": "You are a coding assistant."}


def _human(text: str = "hello") -> dict:
    return {"type": "human", "content": text}


def _ai_text(text: str = "I understand.") -> dict:
    return {"type": "ai", "content": text, "tool_calls": [], "additional_kwargs": {}}


def _ai_tool(call_id: str, tool_name: str = "file_read") -> dict:
    return {
        "type": "ai",
        "content": "",
        "tool_calls": [{"id": call_id, "name": tool_name, "args": {"path": "foo.py"}}],
        "additional_kwargs": {},
    }


def _tool_result(call_id: str, content: str = "result") -> dict:
    return {"type": "tool", "content": content, "tool_call_id": call_id}


def _make_tool_rounds(n: int, content_size: int = 10) -> list[dict]:
    """生成 n 个完整工具轮次：每轮 = AI(tool_calls) + ToolMessage。"""
    msgs: list[dict] = []
    for i in range(n):
        cid = f"cid-{i}"
        msgs.append(_ai_tool(cid))
        msgs.append(_tool_result(cid, "x" * content_size))
    return msgs


# ── estimate_tokens ────────────────────────────────────────────────────────────

def test_estimate_tokens_basic() -> None:
    assert estimate_tokens("hello") == 1
    assert estimate_tokens("a" * 400) == 100


def test_estimate_messages_tokens_empty() -> None:
    assert estimate_messages_tokens([]) == 0


# ── CompactionStats ────────────────────────────────────────────────────────────

def test_compaction_stats_round_trip() -> None:
    s = CompactionStats(snip_deleted_tokens=42, last_compaction_level="snip")
    d = s.to_dict()
    s2 = CompactionStats.from_dict(d)
    assert s2.snip_deleted_tokens == 42
    assert s2.last_compaction_level == "snip"


# ── snip_compact ───────────────────────────────────────────────────────────────

def test_snip_no_action_when_few_rounds() -> None:
    """工具轮次 <= SNIP_TOOL_ROUNDS 时不压缩。"""
    msgs = [_system()] + _make_tool_rounds(SNIP_TOOL_ROUNDS)
    state = _make_state(msgs)
    deleted = snip_compact(state)
    assert deleted == 0
    assert len(state.runtime_messages) == len(msgs)


def test_snip_removes_oldest_rounds() -> None:
    """超出 SNIP_TOOL_ROUNDS 时，删除最旧的完整轮次对。"""
    extra = 3
    total_rounds = SNIP_TOOL_ROUNDS + extra
    msgs = [_system()] + _make_tool_rounds(total_rounds, content_size=100)
    state = _make_state(msgs)
    deleted = snip_compact(state)

    assert deleted > 0
    # 只保留 SNIP_TOOL_ROUNDS 轮 + system
    expected_msg_count = 1 + SNIP_TOOL_ROUNDS * 2  # system + rounds * (ai + tool)
    assert len(state.runtime_messages) == expected_msg_count


def test_snip_preserves_system_message() -> None:
    """snip 之后 system message 必须保留在第一条。"""
    msgs = [_system()] + _make_tool_rounds(SNIP_TOOL_ROUNDS + 2)
    state = _make_state(msgs)
    snip_compact(state)
    assert state.runtime_messages[0]["type"] == "system"


def test_snip_never_orphans_tool_messages() -> None:
    """snip 之后不应该出现没有对应 ai message 的 tool message。"""
    msgs = [_system()] + _make_tool_rounds(SNIP_TOOL_ROUNDS + 4)
    state = _make_state(msgs)
    snip_compact(state)

    messages = state.runtime_messages
    tool_call_ids_in_ai: set[str] = set()
    for m in messages:
        if m.get("type") == "ai":
            for tc in m.get("tool_calls", []):
                tool_call_ids_in_ai.add(tc["id"])

    for m in messages:
        if m.get("type") == "tool":
            assert m["tool_call_id"] in tool_call_ids_in_ai, "orphaned tool message found"


# ── microcompact ───────────────────────────────────────────────────────────────

def test_microcompact_no_action_when_few_rounds() -> None:
    """轮次 <= MICRO_KEEP_ROUNDS 时不压缩。"""
    long_content = "L" * (MICRO_CONTENT_THRESHOLD + 100)
    msgs = [_system()] + _make_tool_rounds(MICRO_KEEP_ROUNDS, content_size=len(long_content))
    state = _make_state(msgs)
    saved = microcompact(state)
    assert saved == 0


def test_microcompact_replaces_old_long_content() -> None:
    """旧轮次中超出阈值的工具结果内容应被替换为占位符。"""
    long_content = "L" * (MICRO_CONTENT_THRESHOLD + 200)
    total_rounds = MICRO_KEEP_ROUNDS + 2
    msgs = [_system()] + _make_tool_rounds(total_rounds, content_size=len(long_content))
    state = _make_state(msgs)
    saved = microcompact(state)

    assert saved > 0
    # 被压缩的 tool message 内容应包含 "compacted" 标记
    compacted = [m for m in state.runtime_messages if "compacted" in str(m.get("content", ""))]
    assert len(compacted) > 0


def test_microcompact_keeps_recent_intact() -> None:
    """最近 MICRO_KEEP_ROUNDS 轮的工具结果不被压缩。"""
    long_content = "R" * (MICRO_CONTENT_THRESHOLD + 200)
    total_rounds = MICRO_KEEP_ROUNDS + 2
    msgs = [_system()] + _make_tool_rounds(total_rounds, content_size=len(long_content))
    state = _make_state(msgs)
    # 记录最后 MICRO_KEEP_ROUNDS 个 tool message 的 content
    tool_msgs_before = [m for m in msgs if m.get("type") == "tool"]
    recent_contents = {m["content"] for m in tool_msgs_before[-MICRO_KEEP_ROUNDS:]}

    microcompact(state)

    tool_msgs_after = [m for m in state.runtime_messages if m.get("type") == "tool"]
    recent_after_contents = {m["content"] for m in tool_msgs_after[-MICRO_KEEP_ROUNDS:]}
    # 最近的 tool 内容应保持不变
    assert recent_contents == recent_after_contents


def test_microcompact_skips_short_content() -> None:
    """短工具结果（<= MICRO_CONTENT_THRESHOLD）即使在旧轮次也不被压缩。"""
    short_content = "short"
    total_rounds = MICRO_KEEP_ROUNDS + 2
    msgs = [_system()] + _make_tool_rounds(total_rounds, content_size=len(short_content))
    state = _make_state(msgs)
    saved = microcompact(state)
    assert saved == 0


# ── ContextProjector ───────────────────────────────────────────────────────────

def test_projector_no_action_within_budget() -> None:
    """消息总 tokens 在预算内时直接返回原列表。"""
    msgs = [_system(), _human("hi"), _ai_text("hello")]
    proj = ContextProjector()
    result = proj.project(msgs, budget_tokens=10_000)
    assert result is msgs


def test_projector_trims_to_budget() -> None:
    """超出预算时应裁剪并保持 token 在预算内。"""
    # 制造大量消息
    msgs = [_system()] + [_human("q" * 100), _ai_text("a" * 100)] * 20
    proj = ContextProjector()
    budget = 200
    result = proj.project(msgs, budget_tokens=budget)
    used = estimate_messages_tokens(result)
    assert used <= budget + 50  # 允许少量超额（单条消息不可拆分）


def test_projector_always_keeps_system() -> None:
    """ProjectionContext 始终保留 system message。"""
    msgs = [_system()] + [_human("x" * 1000)] * 5
    proj = ContextProjector()
    result = proj.project(msgs, budget_tokens=10)
    assert any(m.get("type") == "system" for m in result)


# ── CompactionPipeline ─────────────────────────────────────────────────────────

def test_pipeline_prepare_writes_stats() -> None:
    """prepare() 后 state.metadata 中应有 compaction_stats。"""
    msgs = [_system()] + _make_tool_rounds(SNIP_TOOL_ROUNDS + 2, content_size=600)
    state = _make_state(msgs)
    pipeline = CompactionPipeline()
    asyncio.run(pipeline.prepare(state))

    stats = state.metadata.get("compaction_stats")
    assert stats is not None
    assert isinstance(stats["snip_deleted_tokens"], int)
    assert isinstance(stats["micro_deleted_tokens"], int)


def test_pipeline_idempotent_when_small() -> None:
    """消息很少时，多次调用 prepare() 不应改变消息数。"""
    msgs = [_system(), _human("task"), _ai_text("ok")]
    state = _make_state(msgs)
    pipeline = CompactionPipeline()
    asyncio.run(pipeline.prepare(state))
    asyncio.run(pipeline.prepare(state))
    assert len(state.runtime_messages) == 3


def test_pipeline_project_delegates_to_projector() -> None:
    """pipeline.project() 正常委托给 ContextProjector。"""
    msgs = [_system(), _human("hi"), _ai_text("hi")]
    pipeline = CompactionPipeline()
    result = pipeline.project(msgs, budget_tokens=10_000)
    assert result is msgs  # within budget, same object returned
