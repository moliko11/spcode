"""
packages/memory/compaction.py
─────────────────────────────
Phase B: 无 LLM 的四级压缩流水线（Level 1 snip + Level 2 microcompact）。
Level 3 context-collapse 以只读投影的方式提供（ContextProjector）。
Level 4 autocompact 在 Phase D 实现。

核心原则
- snip / microcompact 直接修改 state.runtime_messages（写时压缩）
- ContextProjector 返回新列表，不修改 state（读时投影）
- 压缩统计写入 state.metadata["compaction_stats"]
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from packages.memory.summarizer import TranscriptSummarizer
    from packages.runtime.models import AgentState

# ── 可调常数 ──────────────────────────────────────────────────────────────────

# snip: 保留最近 N 个完整工具轮次（1 轮 = 1 个 AIMessage(tool_calls) + 对应 ToolMessage）
SNIP_TOOL_ROUNDS: int = 6
# snip: 保留最近 N 轮会话历史（human/assistant 对，非工具循环部分）
SNIP_SESSION_KEEP: int = 4

# microcompact: 保留最近 N 个工具结果不压缩
MICRO_KEEP_ROUNDS: int = 3
# microcompact: 工具结果内容超过此字节数才压缩
MICRO_CONTENT_THRESHOLD: int = 400

# context projector 的默认 token 预算
PROJECTOR_BUDGET_TOKENS: int = 8_000

# autocompact: 总 token 数超过此值时触发 Level 4
AUTOCOMPACT_THRESHOLD: int = 50_000
# autocompact: 压实后保留最近 N 个工具轮次
AUTOCOMPACT_KEEP_ROUNDS: int = 4


# ── token 估算 ────────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """粗略估算：1 token ≈ 4 个字符（中英文混合场景取保守值）。"""
    return max(1, len(text) // 4)


def estimate_message_tokens(msg: dict[str, Any]) -> int:
    total = estimate_tokens(str(msg.get("content", "")))
    for tc in msg.get("tool_calls", []):
        total += estimate_tokens(str(tc))
    return total


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


# ── CompactionStats ───────────────────────────────────────────────────────────

@dataclass(slots=True)
class CompactionStats:
    snip_deleted_tokens: int = 0
    micro_deleted_tokens: int = 0
    collapse_deleted_tokens: int = 0
    auto_deleted_tokens: int = 0
    last_compaction_level: str | None = None
    last_compaction_at: float | None = None
    message_count_before: int = 0
    message_count_after: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "snip_deleted_tokens": self.snip_deleted_tokens,
            "micro_deleted_tokens": self.micro_deleted_tokens,
            "collapse_deleted_tokens": self.collapse_deleted_tokens,
            "auto_deleted_tokens": self.auto_deleted_tokens,
            "last_compaction_level": self.last_compaction_level,
            "last_compaction_at": self.last_compaction_at,
            "message_count_before": self.message_count_before,
            "message_count_after": self.message_count_after,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompactionStats":
        obj = cls()
        for key in (
            "snip_deleted_tokens", "micro_deleted_tokens", "collapse_deleted_tokens",
            "auto_deleted_tokens", "last_compaction_level", "last_compaction_at",
            "message_count_before", "message_count_after",
        ):
            if key in data:
                setattr(obj, key, data[key])
        return obj


def _get_stats(state: "AgentState") -> CompactionStats:
    raw = state.metadata.get("compaction_stats")
    if isinstance(raw, dict):
        return CompactionStats.from_dict(raw)
    return CompactionStats()


def _save_stats(state: "AgentState", stats: CompactionStats) -> None:
    state.metadata["compaction_stats"] = stats.to_dict()


# ── 工具轮次识别 ──────────────────────────────────────────────────────────────

def _find_tool_rounds(messages: list[dict[str, Any]]) -> list[tuple[int, list[int]]]:
    """
    从消息列表中识别完整的工具轮次。
    返回列表：每项 = (ai_message_index, [tool_message_index...])
    按出现顺序排列（最老在前）。
    """
    rounds: list[tuple[int, list[int]]] = []
    i = len(messages) - 1
    while i >= 0:
        msg = messages[i]
        if msg.get("type") == "tool":
            # 收集紧邻的所有 tool messages（同一个 AI call 可能触发多个）
            tool_indices: list[int] = []
            j = i
            while j >= 0 and messages[j].get("type") == "tool":
                tool_indices.insert(0, j)
                j -= 1
            # j 应指向触发这些工具的 AIMessage
            if j >= 0 and messages[j].get("type") == "ai" and messages[j].get("tool_calls"):
                rounds.insert(0, (j, tool_indices))
                i = j - 1
            else:
                # 未能配对，跳过（不删除孤立消息）
                i -= 1
        else:
            i -= 1
    return rounds


# ── Level 1: snip compact ─────────────────────────────────────────────────────

def snip_compact(state: "AgentState") -> int:
    """
    Level 1：保留最近 SNIP_TOOL_ROUNDS 个完整工具轮次 + 最近 SNIP_SESSION_KEEP 轮会话历史。
    只删除完整的 (AI+tool) 对，永远不删孤立消息。
    返回估算删除的 token 数。
    """
    messages = state.runtime_messages
    rounds = _find_tool_rounds(messages)

    if len(rounds) <= SNIP_TOOL_ROUNDS:
        return 0  # 工具轮次不多，不需要 snip

    # 要删除的旧轮次
    old_rounds = rounds[: len(rounds) - SNIP_TOOL_ROUNDS]
    delete_indices: set[int] = set()
    for ai_idx, tool_indices in old_rounds:
        delete_indices.add(ai_idx)
        delete_indices.update(tool_indices)

    deleted_tokens = sum(estimate_message_tokens(messages[i]) for i in delete_indices)
    state.runtime_messages = [m for idx, m in enumerate(messages) if idx not in delete_indices]
    return deleted_tokens


# ── Level 2: microcompact ─────────────────────────────────────────────────────

def microcompact(state: "AgentState") -> int:
    """
    Level 2：将旧工具轮次（超出 MICRO_KEEP_ROUNDS 轮前）的长工具结果内容替换为轻量占位。
    不删除任何消息，只压缩内容。
    返回估算节省的 token 数。
    """
    messages = state.runtime_messages
    rounds = _find_tool_rounds(messages)

    if len(rounds) <= MICRO_KEEP_ROUNDS:
        return 0

    # 要压缩的旧轮次中的 tool message indices
    old_rounds = rounds[: len(rounds) - MICRO_KEEP_ROUNDS]
    compact_tool_indices: set[int] = set()
    for _, tool_indices in old_rounds:
        compact_tool_indices.update(tool_indices)

    saved_tokens = 0
    for idx in compact_tool_indices:
        msg = messages[idx]
        content = str(msg.get("content", ""))
        if len(content) <= MICRO_CONTENT_THRESHOLD:
            continue  # 已经很短，不压缩
        tool_call_id = msg.get("tool_call_id", "")
        first_line = content.split("\n")[0].strip()[:80]
        placeholder = f"[compacted | id={tool_call_id} | preview={first_line!r}]"
        saved_tokens += estimate_tokens(content) - estimate_tokens(placeholder)
        messages[idx] = {**msg, "content": placeholder}

    return saved_tokens


# ── Level 4: autocompact ─────────────────────────────────────────────────────

async def autocompact(
    state: "AgentState",
    summarizer: "TranscriptSummarizer",
    archive_dir: Path | None = None,
) -> int:
    """
    Level 4：总 token 数超过 AUTOCOMPACT_THRESHOLD 时触发。
    将旧消息归档到 JSONL 文件（若 archive_dir 不为 None），并用 LLM 生成的摘要
    单条 system 消息替换，保留最近 AUTOCOMPACT_KEEP_ROUNDS 个完整工具轮次。
    返回估算节省的 token 数。
    """
    messages = state.runtime_messages
    if estimate_messages_tokens(messages) <= AUTOCOMPACT_THRESHOLD:
        return 0

    system_msgs = [m for m in messages if m.get("type") == "system"]
    non_system = [m for m in messages if m.get("type") != "system"]

    rounds = _find_tool_rounds(non_system)
    keep_count = min(AUTOCOMPACT_KEEP_ROUNDS, len(rounds))

    if keep_count > 0:
        keep_start_idx = rounds[-keep_count][0]
        to_archive = non_system[:keep_start_idx]
        to_keep = non_system[keep_start_idx:]
    else:
        # フォールバック：工具轮次为零，保留末尾 4 条
        cut = max(0, len(non_system) - 4)
        to_archive = non_system[:cut]
        to_keep = non_system[cut:]

    if not to_archive:
        return 0

    # 归档到文件
    if archive_dir is not None:
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        fname = archive_dir / f"{state.run_id}_{ts}.jsonl"
        with fname.open("w", encoding="utf-8") as fh:
            for msg in to_archive:
                fh.write(json.dumps(msg, ensure_ascii=False) + "\n")

    # LLM 摘要
    summary_text = await summarizer.summarize(to_archive, task=state.task)
    summary_msg: dict[str, Any] = {
        "type": "system",
        "content": f"[Autocompacted history — step {state.step}]:\n{summary_text}",
    }

    archived_tokens = estimate_messages_tokens(to_archive)
    summary_tokens = estimate_message_tokens(summary_msg)
    saved = max(0, archived_tokens - summary_tokens)
    state.runtime_messages = system_msgs + [summary_msg] + to_keep
    return saved


# ── Level 3: ContextProjector（读时投影，不修改 state）────────────────────────

class ContextProjector:
    """
    读时视图构造器：在不修改 state.runtime_messages 的前提下，
    根据 token 预算返回一个更小的消息列表。

    策略：
    - 始终保留 SystemMessage
    - 从消息列表末尾开始添加，直到达到 token 预算
    - 优先保留最近消息
    """

    def project(
        self,
        messages: list[dict[str, Any]],
        budget_tokens: int = PROJECTOR_BUDGET_TOKENS,
    ) -> list[dict[str, Any]]:
        total = estimate_messages_tokens(messages)
        if total <= budget_tokens:
            return messages

        # 系统消息始终保留
        system_msgs = [m for m in messages if m.get("type") == "system"]
        non_system = [m for m in messages if m.get("type") != "system"]

        system_tokens = estimate_messages_tokens(system_msgs)
        remaining = budget_tokens - system_tokens
        if remaining <= 0:
            return system_msgs

        # 从末尾累加，直到预算耗尽
        kept: list[dict[str, Any]] = []
        for msg in reversed(non_system):
            cost = estimate_message_tokens(msg)
            if remaining >= cost:
                kept.insert(0, msg)
                remaining -= cost

        return system_msgs + kept

    def estimate(self, messages: list[dict[str, Any]]) -> int:
        return estimate_messages_tokens(messages)


# ── CompactionPipeline ────────────────────────────────────────────────────────

class CompactionPipeline:
    """
    统一压缩入口。每次模型调用前调用 prepare()。

    Level 1 snip：删除超限旧工具轮次对
    Level 2 microcompact：压缩旧工具结果的内容
    Level 3 由 project() 方法提供只读投影
    Level 4 autocompact：LLM 摘要替换旧消息（Phase D，需提供 summarizer）
    """

    def __init__(
        self,
        summarizer: "TranscriptSummarizer | None" = None,
        archive_dir: "Path | None" = None,
    ) -> None:
        self.projector = ContextProjector()
        self.summarizer = summarizer
        self.archive_dir: Path | None = archive_dir

    async def prepare(self, state: "AgentState") -> None:
        """模型调用前执行，in-place 修改 state.runtime_messages。"""
        stats = _get_stats(state)
        stats.message_count_before = len(state.runtime_messages)

        # Level 4: autocompact（优先于 snip/micro，仅在超预算时触发）
        if self.summarizer is not None:
            auto_saved = await autocompact(state, self.summarizer, self.archive_dir)
            if auto_saved > 0:
                stats.auto_deleted_tokens += auto_saved
                stats.last_compaction_level = "auto"
                stats.last_compaction_at = time.time()

        # Level 1: snip
        snip_deleted = snip_compact(state)
        if snip_deleted > 0:
            stats.snip_deleted_tokens += snip_deleted
            stats.last_compaction_level = "snip"
            stats.last_compaction_at = time.time()

        # Level 2: microcompact
        micro_saved = microcompact(state)
        if micro_saved > 0:
            stats.micro_deleted_tokens += micro_saved
            stats.last_compaction_level = "micro"
            stats.last_compaction_at = time.time()

        stats.message_count_after = len(state.runtime_messages)
        _save_stats(state, stats)

    def project(
        self,
        messages: list[dict[str, Any]],
        budget_tokens: int = PROJECTOR_BUDGET_TOKENS,
    ) -> list[dict[str, Any]]:
        """Level 3 读时投影，不修改 state。"""
        return self.projector.project(messages, budget_tokens)

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return estimate_messages_tokens(messages)
