from __future__ import annotations

import asyncio
import collections
import itertools
import json
from pathlib import Path
from typing import AsyncIterator, Protocol

from .config import AUDIT_LOG_PATH, logger
from .models import AgentEvent, EventType, _EVENTTYPE_TO_KIND


class EventSubscriber(Protocol):
    """
    事件订阅器
    """
    async def handle(self, event: AgentEvent) -> None:
        ...


class EventBus:
    """
    事件总线（A2 改造）。
    - 内置 ring buffer（默认 2000 条），支持 SSE 断线续传 replay
    - seq 单调递增，在 publish 时注入到 event.seq
    - event_kind 按映射表自动填充（若调用方未显式设置）
    - 内存 Queue fan-out 给 subscribe_stream() 消费者
    - 持久化订阅者（LoggingSubscriber/AuditSubscriber）并发 gather，不阻塞主循环
    """

    def __init__(self, ring_size: int = 2000) -> None:
        self._subscribers: list[EventSubscriber] = []
        self._ring: collections.deque[AgentEvent] = collections.deque(maxlen=ring_size)
        self._seq_counter = itertools.count(1)
        # run_id → list[asyncio.Queue]（每个 SSE 连接一个 Queue）
        self._stream_queues: dict[str, list[asyncio.Queue[AgentEvent | None]]] = {}

    def subscribe(self, subscriber: EventSubscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    async def publish(self, event: AgentEvent) -> None:
        # 注入 seq
        event.seq = next(self._seq_counter)
        # 自动填充 event_kind（旧路径兼容）
        if not event.event_kind and event.event_type in _EVENTTYPE_TO_KIND:
            event.event_kind = _EVENTTYPE_TO_KIND[event.event_type].value
        # 写入 ring buffer
        self._ring.append(event)
        # 推入所有 SSE 流 Queue（不阻塞）
        for q in self._stream_queues.get(event.run_id, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # 消费者太慢时丢弃，不影响主循环
        # 并发调用持久化订阅者，异常不传播
        if self._subscribers:
            await asyncio.gather(
                *[s.handle(event) for s in self._subscribers],
                return_exceptions=True,
            )

    def get_ring_snapshot(self, run_id: str, after_seq: int = 0) -> list[AgentEvent]:
        """返回 ring buffer 中属于 run_id 且 seq > after_seq 的历史事件（用于 SSE replay）。"""
        return [e for e in self._ring if e.run_id == run_id and e.seq > after_seq]

    async def subscribe_stream(
        self,
        run_id: str,
        after_seq: int = 0,
        sentinel: object = None,
    ) -> AsyncIterator[AgentEvent]:
        """
        异步生成器：先 replay ring buffer 历史，再持续推送新事件。

        调用方在 run 完成后需调用 close_stream(run_id) 推入 sentinel 结束迭代。
        """
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue(maxsize=500)
        self._stream_queues.setdefault(run_id, []).append(queue)
        try:
            for event in self.get_ring_snapshot(run_id, after_seq):
                yield event
            while True:
                item = await queue.get()
                if item is None:  # sentinel → 结束
                    break
                yield item
        finally:
            queues = self._stream_queues.get(run_id, [])
            if queue in queues:
                queues.remove(queue)

    def close_stream(self, run_id: str) -> None:
        """向指定 run_id 的所有 Queue 推入 None sentinel，结束 subscribe_stream 迭代。"""
        for q in self._stream_queues.get(run_id, []):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass


class LoggingSubscriber:
    """
    日志事件订阅器
    """
    async def handle(self, event: AgentEvent) -> None:
        kind = event.event_kind or event.event_type.value
        logger.info(
            "event=%s seq=%d run_id=%s step=%s payload_keys=%s",
            kind,
            event.seq,
            event.run_id,
            event.step,
            list(event.payload.keys()),
        )


class AuditSubscriber:
    """
    审计事件订阅器（A2 改造：文件写入改为 asyncio.to_thread 非阻塞）
    """
    _AUDIT_KINDS = {
        EventType.TOOL_STARTED,
        EventType.TOOL_FINISHED,
        EventType.TOOL_FAILED,
        EventType.HUMAN_REQUIRED,
        EventType.HUMAN_APPROVED,
        EventType.HUMAN_REJECTED,
    }

    def __init__(self, path: Path = AUDIT_LOG_PATH) -> None:
        self.path = path

    async def handle(self, event: AgentEvent) -> None:
        if event.event_type not in self._AUDIT_KINDS:
            return
        record = {
            "ts": event.ts,
            "seq": event.seq,
            "run_id": event.run_id,
            "step": event.step,
            "event_type": event.event_type.value,
            "event_kind": event.event_kind,
            "payload": event.payload,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        await asyncio.to_thread(self._write_line, line)

    def _write_line(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
