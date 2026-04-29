from __future__ import annotations

import time

import pytest

from packages.app_service.chat_service import ChatService
from packages.runtime.events import EventBus
from packages.runtime.models import AgentEvent, AgentState, EventKind, EventType, Phase, RunStatus


class _FakeRuntime:
    def __init__(self) -> None:
        self.event_bus = EventBus()

    async def chat(self, user_id: str, session_id: str, message: str) -> AgentState:
        await self.event_bus.publish(
            AgentEvent(
                run_id="run-1",
                event_type=EventType.RUN_STARTED,
                event_kind=EventKind.run_started.value,
                ts=time.time(),
                step=0,
                payload={"task": message, "session_id": session_id},
            )
        )
        await self.event_bus.publish(
            AgentEvent(
                run_id="run-1",
                event_type=EventType.MODEL_OUTPUT,
                event_kind=EventKind.model_token.value,
                ts=time.time(),
                step=0,
                payload={"token": "Hi"},
            )
        )
        return AgentState(
            run_id="run-1",
            user_id=user_id,
            task=message,
            session_id=session_id,
            status=RunStatus.COMPLETED,
            phase=Phase.COMPLETED,
            final_output="Hi",
            metadata={"cost_summary": {"total_tokens": 2}},
        )

    async def resume(self, run_id: str, human_decision: dict) -> AgentState:
        await self.event_bus.publish(
            AgentEvent(
                run_id=run_id,
                event_type=EventType.MODEL_OUTPUT,
                event_kind=EventKind.model_token.value,
                ts=time.time(),
                step=1,
                payload={"token": " again"},
            )
        )
        return AgentState(
            run_id=run_id,
            user_id="u",
            task="t",
            session_id="s",
            status=RunStatus.COMPLETED,
            phase=Phase.COMPLETED,
            final_output="again",
            metadata={"cost_summary": {"total_tokens": 4}},
        )


@pytest.mark.asyncio
async def test_chat_stream_invokes_event_callback() -> None:
    svc = ChatService(runtime=_FakeRuntime())
    events: list[str] = []

    async def _on_event(event: AgentEvent) -> None:
        events.append(event.event_kind)

    result = await svc.chat_stream(
        user_id="u1",
        session_id="s1",
        message="hello",
        on_event=_on_event,
    )

    assert events == [EventKind.run_started.value, EventKind.model_token.value]
    assert result.final_output == "Hi"
    assert result.run_id == "run-1"


@pytest.mark.asyncio
async def test_approve_stream_invokes_event_callback() -> None:
    from packages.app_service.chat_service import HumanDecision

    svc = ChatService(runtime=_FakeRuntime())
    events: list[str] = []

    result = await svc.approve_stream(
        run_id="run-2",
        decision=HumanDecision(approved=True),
        on_event=lambda event: events.append(event.event_kind),
    )

    assert events == [EventKind.model_token.value]
    assert result.final_output == "again"