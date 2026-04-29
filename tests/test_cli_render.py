from __future__ import annotations

from packages.cli.render import StreamToolCallAggregator, build_stream_event_view
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


def test_tool_call_aggregator_flushes_compact_summary() -> None:
    aggregator = StreamToolCallAggregator()

    views, handled = aggregator.ingest(
        {"kind": "model.tool_call_delta", "payload": {"delta": {"name": "bash", "id": "call-1", "index": 0}}}
    )
    assert handled is True
    assert len(views) == 1
    assert views[0].text == "bash[0] building arguments..."

    views, handled = aggregator.ingest(
        {"kind": "model.tool_call_delta", "payload": {"delta": {"id": "call-1", "index": 0, "args": '{"command":"echo hi","timeout_s":30}'}}}
    )
    assert handled is True
    assert views == []

    views, handled = aggregator.ingest({"kind": "tool.started", "payload": {"tool_name": "bash"}})
    assert handled is False
    assert len(views) == 1
    assert views[0].category == "tool_call"
    assert "command=echo hi" in views[0].text
    assert "timeout_s=30" in views[0].text


def test_tool_call_aggregator_keeps_parallel_indexes_separate() -> None:
    aggregator = StreamToolCallAggregator()

    aggregator.ingest({"kind": "model.tool_call_delta", "payload": {"delta": {"name": "bash", "index": 0}}})
    aggregator.ingest({"kind": "model.tool_call_delta", "payload": {"delta": {"index": 0, "args": '{"command":"a"}'}}})
    aggregator.ingest({"kind": "model.tool_call_delta", "payload": {"delta": {"name": "bash", "index": 1}}})
    aggregator.ingest({"kind": "model.tool_call_delta", "payload": {"delta": {"index": 1, "args": '{"command":"b"}'}}})

    views = aggregator.flush()

    assert len(views) == 2
    assert any("bash[0] command=a" in view.text for view in views)
    assert any("bash[1] command=b" in view.text for view in views)