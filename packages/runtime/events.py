from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .config import AUDIT_LOG_PATH, logger
from .models import AgentEvent, EventType


class EventSubscriber(Protocol):
    async def handle(self, event: AgentEvent) -> None:
        ...


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[EventSubscriber] = []

    def subscribe(self, subscriber: EventSubscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    async def publish(self, event: AgentEvent) -> None:
        for subscriber in tuple(self._subscribers):
            try:
                await subscriber.handle(event)
            except Exception:
                logger.exception("event subscriber failed: %s", subscriber.__class__.__name__)


class LoggingSubscriber:
    async def handle(self, event: AgentEvent) -> None:
        logger.info(
            "event=%s run_id=%s step=%s payload=%s",
            event.event_type.value,
            event.run_id,
            event.step,
            event.payload,
        )


class AuditSubscriber:
    def __init__(self, path: Path = AUDIT_LOG_PATH) -> None:
        self.path = path

    async def handle(self, event: AgentEvent) -> None:
        if event.event_type not in {
            EventType.TOOL_STARTED,
            EventType.TOOL_FINISHED,
            EventType.TOOL_FAILED,
            EventType.HUMAN_REQUIRED,
            EventType.HUMAN_APPROVED,
            EventType.HUMAN_REJECTED,
        }:
            return
        record = {
            "ts": event.ts,
            "run_id": event.run_id,
            "step": event.step,
            "event_type": event.event_type.value,
            "payload": event.payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
