from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage

from packages.runtime.agent_loop import AgentRuntime
from packages.runtime.bootstrap import build_runtime
from packages.runtime.models import serialize_message


def test_build_runtime_smoke() -> None:
    runtime = build_runtime()
    assert runtime.registry.get_spec("tool_search").name == "tool_search"
    assert runtime.registry.get_spec("file_read").name == "file_read"
    assert runtime.session_store is not None
    assert runtime.memory_manager is not None


def test_session_store_empty_load() -> None:
    runtime = build_runtime()
    messages = asyncio.run(runtime.session_store.load_messages("runtime_bootstrap_missing"))
    assert messages == []


def test_preserve_reasoning_kwargs_for_trimmed_ai_message() -> None:
    runtime = object.__new__(AgentRuntime)
    response = AIMessage(
        content="",
        additional_kwargs={
            "reasoning_content": "thinking trace",
            "tool_calls": [{"id": "call_1"}],
        },
    )

    assert runtime._assistant_reasoning_kwargs(response) == {"reasoning_content": "thinking trace"}


def test_serialize_ai_message_preserves_response_metadata_reasoning() -> None:
    message = AIMessage(
        content="",
        response_metadata={"reasoning_content": "thinking trace"},
    )

    data = serialize_message(message)

    assert data["additional_kwargs"]["reasoning_content"] == "thinking trace"
