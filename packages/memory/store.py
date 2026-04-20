from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .models import CompactionRecord, MemoryEntry, MemoryType


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, MemoryType):
        return obj.value
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def _entry_to_dict(entry: MemoryEntry) -> dict[str, Any]:
    return {
        "memory_id": entry.memory_id,
        "user_id": entry.user_id,
        "memory_type": entry.memory_type.value,
        "content": entry.content,
        "summary": entry.summary,
        "tags": entry.tags,
        "importance": entry.importance,
        "created_at": entry.created_at,
        "session_id": entry.session_id,
        "run_id": entry.run_id,
        "workspace_id": entry.workspace_id,
        "last_accessed": entry.last_accessed,
        "access_count": entry.access_count,
        "source": entry.source,
        "metadata": _to_jsonable(entry.metadata),
    }


def _dict_to_entry(data: dict[str, Any]) -> MemoryEntry:
    return MemoryEntry(
        memory_id=data["memory_id"],
        user_id=data["user_id"],
        memory_type=MemoryType(data["memory_type"]),
        content=data["content"],
        summary=data["summary"],
        tags=data.get("tags", []),
        importance=float(data.get("importance", 0.5)),
        created_at=float(data["created_at"]),
        session_id=data.get("session_id"),
        run_id=data.get("run_id"),
        workspace_id=data.get("workspace_id"),
        last_accessed=data.get("last_accessed"),
        access_count=int(data.get("access_count", 0)),
        source=data.get("source", "run_summary"),
        metadata=dict(data.get("metadata", {})),
    )


class FileMemoryStore:
    """
    v1 实现：按 user_id 存 JSONL，每行一条 MemoryEntry。
    目录结构：
        runtime_data/memory/users/<user_id>.jsonl
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        return self.root / f"{user_id}.jsonl"

    async def save(self, entry: MemoryEntry) -> None:
        with self._path(entry.user_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_entry_to_dict(entry), ensure_ascii=False) + "\n")

    async def list_recent(
        self,
        user_id: str,
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[MemoryEntry]:
        path = self._path(user_id)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        entries = [_dict_to_entry(json.loads(line)) for line in lines if line.strip()]
        if workspace_id is not None:
            entries = [e for e in entries if e.workspace_id is None or e.workspace_id == workspace_id]
        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries[:limit]

    async def search(
        self,
        query: str,
        user_id: str,
        limit: int = 5,
        memory_types: list[MemoryType] | None = None,
        workspace_id: str | None = None,
    ) -> list[MemoryEntry]:
        """
        v1 关键词检索：对 content/summary/tags 做简单字符串匹配，按 importance 排序。
        """
        path = self._path(user_id)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        all_entries = [_dict_to_entry(json.loads(line)) for line in lines if line.strip()]

        query_lower = query.lower()
        keywords = query_lower.split()

        def _score(entry: MemoryEntry) -> float:
            text = (entry.content + " " + entry.summary + " " + " ".join(entry.tags)).lower()
            hit_count = sum(1 for kw in keywords if kw in text)
            # 优先注入 semantic 和 procedural
            type_bonus = 0.1 if entry.memory_type in (MemoryType.SEMANTIC, MemoryType.PROCEDURAL) else 0.0
            access_bonus = min(entry.access_count * 0.01, 0.1)
            return hit_count + entry.importance + type_bonus + access_bonus

        candidates = all_entries
        if workspace_id is not None:
            candidates = [e for e in candidates if e.workspace_id is None or e.workspace_id == workspace_id]
        if memory_types:
            candidates = [e for e in candidates if e.memory_type in memory_types]

        scored = [(e, _score(e)) for e in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [e for e, _ in scored[:limit]]

    async def update_access(self, memory_id: str, user_id: str) -> None:
        """更新一条记忆的访问时间和次数（重写整个文件）。"""
        path = self._path(user_id)
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        updated: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("memory_id") == memory_id:
                data["last_accessed"] = time.time()
                data["access_count"] = int(data.get("access_count", 0)) + 1
            updated.append(json.dumps(data, ensure_ascii=False))
        path.write_text("\n".join(updated) + "\n", encoding="utf-8")

    async def delete(self, memory_id: str, user_id: str) -> None:
        path = self._path(user_id)
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if line.strip() and json.loads(line).get("memory_id") != memory_id]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")


class TranscriptArchiveStore:
    """
    保存 autocompact 前的完整 runtime_messages 快照。
    目录结构：
        runtime_data/memory/transcripts/<session_id>/<run_id>-full.json
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, session_id: str, run_id: str, messages: list[dict[str, Any]]) -> Path:
        target_dir = self.root / session_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{run_id}-full.json"
        path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, session_id: str, run_id: str) -> list[dict[str, Any]] | None:
        path = self.root / session_id / f"{run_id}-full.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


class CompactionStateStore:
    """
    持久化每个 session 的压缩状态（CompactionRecord 列表）。
    目录结构：
        runtime_data/memory/compaction/<session_id>.json
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def append(self, session_id: str, record: CompactionRecord) -> None:
        path = self._path(session_id)
        records: list[dict[str, Any]] = []
        if path.exists():
            records = json.loads(path.read_text(encoding="utf-8"))
        records.append({
            "level": record.level,
            "created_at": record.created_at,
            "before_tokens": record.before_tokens,
            "after_tokens": record.after_tokens,
            "deleted_tokens": record.deleted_tokens,
            "reason": record.reason,
            "source_message_ids": record.source_message_ids,
            "transcript_ref": record.transcript_ref,
            "summary_ref": record.summary_ref,
        })
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, session_id: str) -> list[CompactionRecord]:
        path = self._path(session_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [
            CompactionRecord(
                level=d["level"],
                created_at=float(d["created_at"]),
                before_tokens=int(d["before_tokens"]),
                after_tokens=int(d["after_tokens"]),
                deleted_tokens=int(d["deleted_tokens"]),
                reason=d["reason"],
                source_message_ids=d.get("source_message_ids", []),
                transcript_ref=d.get("transcript_ref"),
                summary_ref=d.get("summary_ref"),
            )
            for d in data
        ]
