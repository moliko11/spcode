from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any


class MemoryType(str, enum.Enum):
    EPISODE = "episode"       # 某次运行做了什么
    SEMANTIC = "semantic"     # 项目结构、偏好、约束、接口知识
    PROCEDURAL = "procedural" # 成功流程、修复套路、失败规避策略


@dataclass(slots=True)
class MemoryEntry:
    memory_id: str
    user_id: str
    memory_type: MemoryType
    content: str        # 完整记忆正文
    summary: str        # 一行摘要，用于快速展示与注入
    tags: list[str]
    importance: float   # 0.0~1.0
    created_at: float
    session_id: str | None = None
    run_id: str | None = None
    workspace_id: str | None = None  # 绝对路径 str，None 表示跨项目通用记忆
    last_accessed: float | None = None
    access_count: int = 0
    source: str = "run_summary"  # run_summary / user_feedback / tool_observation
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RecallPack:
    """单次检索请求的结果包，直接用于 context 注入。"""
    query: str
    items: list[MemoryEntry]
    injected_text: str  # 格式化后可直接拼接到 system prompt 的字符串
    generated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class CompactionRecord:
    """一次压缩操作的元数据记录。"""
    level: str          # snip / micro / collapse / auto
    created_at: float
    before_tokens: int
    after_tokens: int
    deleted_tokens: int
    reason: str
    source_message_ids: list[str] = field(default_factory=list)
    transcript_ref: str | None = None   # autocompact 时完整转录的落盘路径
    summary_ref: str | None = None      # autocompact 时摘要文件路径
