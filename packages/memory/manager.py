from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

from .models import MemoryEntry, MemoryType, RecallPack
from .store import FileMemoryStore

if TYPE_CHECKING:
    from packages.runtime.models import AgentState


class MemoryManager:
    """
    记忆系统的统一对外接口。
    v1：基于 FileMemoryStore 的规则提取 + 关键词检索，无 LLM 参与。
    """

    def __init__(self, store: FileMemoryStore, workspace_id: str | None = None) -> None:
        self.store = store
        self.workspace_id = workspace_id  # 当前项目的绝对路径，用于隔离多项目记忆

    # ------------------------------------------------------------------
    # 写入侧
    # ------------------------------------------------------------------

    async def remember_run(self, state: "AgentState") -> list[MemoryEntry]:
        """
        运行结束后，从 AgentState 中提炼长期记忆候选项并写入存储。
        v1：纯规则提取。
        """
        entries: list[MemoryEntry] = []

        # 1. Episode 记忆：记录本次任务做了什么
        episode_content = _build_episode_content(state)
        episode = MemoryEntry(
            memory_id=str(uuid.uuid4()),
            user_id=state.user_id,
            session_id=state.session_id,
            run_id=state.run_id,
            workspace_id=self.workspace_id,
            memory_type=MemoryType.EPISODE,
            content=episode_content,
            summary=f"[{state.status.value}] {state.task[:80]}",
            tags=_extract_tags(state),
            importance=_episode_importance(state),
            created_at=time.time(),
            source="run_summary",
            metadata={
                "step_count": state.step,
                "tool_names": list({r.tool_name for r in state.tool_results}),
                "changed_files": _extract_changed_files(state),
            },
        )
        await self.store.save(episode)
        entries.append(episode)

        # 2. Semantic 记忆：从 changed_files / tool_results 中提炼项目结构知识
        semantic_entries = _extract_semantic_entries(state, workspace_id=self.workspace_id)
        for sem in semantic_entries:
            await self.store.save(sem)
        entries.extend(semantic_entries)

        return entries

    async def update_semantic_memory(self, user_id: str, key: str, value: str) -> None:
        """主动更新一条语义记忆，例如项目结构变化时。"""
        entry = MemoryEntry(
            memory_id=str(uuid.uuid4()),
            user_id=user_id,
            workspace_id=self.workspace_id,
            memory_type=MemoryType.SEMANTIC,
            content=f"{key}: {value}",
            summary=f"{key}: {value[:60]}",
            tags=[key],
            importance=0.7,
            created_at=time.time(),
            source="user_feedback",
        )
        await self.store.save(entry)

    # ------------------------------------------------------------------
    # 读取侧
    # ------------------------------------------------------------------

    async def recall(self, query: str, user_id: str, limit: int = 5) -> RecallPack:
        """
        检索与当前任务最相关的记忆，返回 RecallPack（含可直接注入的文本）。
        """
        items = await self.store.search(query, user_id, limit=limit, workspace_id=self.workspace_id)
        injected_text = _format_recall_text(items)
        pack = RecallPack(
            query=query,
            items=items,
            injected_text=injected_text,
        )
        # 更新访问计数
        for item in items:
            await self.store.update_access(item.memory_id, user_id)
        return pack

    async def list_recent(self, user_id: str, limit: int = 10) -> list[MemoryEntry]:
        return await self.store.list_recent(user_id, limit=limit, workspace_id=self.workspace_id)

    async def forget(self, memory_id: str, user_id: str) -> None:
        await self.store.delete(memory_id, user_id)


# ------------------------------------------------------------------
# 内部辅助函数（规则提取，无 LLM）
# ------------------------------------------------------------------

def _build_episode_content(state: "AgentState") -> str:
    lines = [
        f"Task: {state.task}",
        f"Status: {state.status.value}",
        f"Steps: {state.step}",
    ]
    if state.final_output:
        lines.append(f"Output: {state.final_output[:300]}")
    if state.failure_reason:
        lines.append(f"Failure: {state.failure_reason[:200]}")
    changed = _extract_changed_files(state)
    if changed:
        lines.append("Changed files: " + ", ".join(changed[:10]))
    tool_names = sorted({r.tool_name for r in state.tool_results})
    if tool_names:
        lines.append("Tools used: " + ", ".join(tool_names))
    return "\n".join(lines)


def _extract_tags(state: "AgentState") -> list[str]:
    tags: set[str] = set()
    tags.add(state.status.value)
    for r in state.tool_results:
        tags.add(r.tool_name)
    for f in _extract_changed_files(state):
        parts = f.replace("\\", "/").split("/")
        if len(parts) >= 2:
            tags.add(parts[-2])  # 上级目录名
    return sorted(tags)


def _episode_importance(state: "AgentState") -> float:
    """越多文件变更 / 越多工具调用 / 越多步骤 → 重要性越高。"""
    changed_count = len(_extract_changed_files(state))
    tool_count = len(state.tool_results)
    base = 0.4
    bonus = min(changed_count * 0.05 + tool_count * 0.02 + state.step * 0.01, 0.5)
    return round(base + bonus, 2)


def _extract_changed_files(state: "AgentState") -> list[str]:
    files: list[str] = []
    for r in state.tool_results:
        files.extend(r.changed_files or [])
    return list(dict.fromkeys(files))  # 去重保序


def _extract_semantic_entries(state: "AgentState", workspace_id: str | None = None) -> list[MemoryEntry]:
    """
    从工具结果中提炼项目结构相关的语义记忆。
    v1：只针对 file_write / file_edit 产生的文件路径生成 semantic 条目。
    """
    entries: list[MemoryEntry] = []
    seen: set[str] = set()
    for r in state.tool_results:
        if r.tool_name not in ("file_write", "file_edit"):
            continue
        for f in r.changed_files or []:
            if f in seen:
                continue
            seen.add(f)
            norm = f.replace("\\", "/")
            entry = MemoryEntry(
                memory_id=str(uuid.uuid4()),
                user_id=state.user_id,
                session_id=state.session_id,
                run_id=state.run_id,
                workspace_id=workspace_id,
                memory_type=MemoryType.SEMANTIC,
                content=f"File modified: {norm}",
                summary=f"Modified {norm}",
                tags=["file_change", norm.split("/")[-1]],
                importance=0.5,
                created_at=time.time(),
                source="tool_observation",
                metadata={"tool": r.tool_name},
            )
            entries.append(entry)
    return entries


def _format_recall_text(items: list[MemoryEntry]) -> str:
    if not items:
        return ""
    lines = ["Relevant memory:"]
    for item in items:
        prefix = {
            MemoryType.EPISODE: "Recent run",
            MemoryType.SEMANTIC: "Project fact",
            MemoryType.PROCEDURAL: "Procedural note",
        }.get(item.memory_type, "Note")
        lines.append(f"- {prefix}: {item.summary}")
    return "\n".join(lines)
