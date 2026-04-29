from __future__ import annotations

from packages.cli.render import build_stream_event_view
from packages.runtime.models import AgentEvent, EventKind, EventType


def test_build_stream_event_view_for_agent_token_event() -> None:
    event = AgentEvent(
        run_id="run-1",
        event_type=EventType.MODEL_OUTPUT,
        event_kind=EventKind.model_token.value,
        ts=1.0,
        step=0,
        payload={"token": "Hi"},
    )

    view = build_stream_event_view(event)

    assert view.category == "token"
    assert view.label == "token"
    assert view.text == "Hi"


def test_build_stream_event_view_for_dict_run_event() -> None:
    view = build_stream_event_view(
        {"kind": "run.failed", "error": "boom", "payload": {}, "step": 2}
    )

    assert view.category == "run"
    assert view.kind == "run.failed"
    assert view.text == "boom"
    assert view.terminal_status == "failed"


def test_build_stream_event_view_for_usage_event() -> None:
    view = build_stream_event_view(
        {"kind": "model.usage", "payload": {"total_tokens": 42}}
    )

    assert view.category == "usage"
    assert view.text == "total_tokens=42"


def test_build_stream_event_view_for_tool_started_event() -> None:
    view = build_stream_event_view(
        {"kind": "tool.started", "payload": {"tool_name": "bash", "risk_level": "high"}}
    )

    assert view.category == "tool"
    assert view.label == "tool.started"
    assert view.text == "bash | risk=high"


def test_build_stream_event_view_for_tool_call_delta_event() -> None:
    view = build_stream_event_view(
        {"kind": "model.tool_call_delta", "payload": {"delta": {"name": "grep", "arguments": "{\"q\":\"x\"}"}}}
    )

    assert view.category == "tool_call"
    assert view.label == "tool_call"
    assert "grep" in view.text