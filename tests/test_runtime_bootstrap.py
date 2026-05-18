from __future__ import annotations

import asyncio
from pathlib import Path

from langchain_core.messages import AIMessage

from packages.runtime.agent_loop import AgentRuntime
from packages.runtime.bootstrap import build_runtime
from packages.runtime.models import AgentState, serialize_message


def test_build_runtime_smoke() -> None:
    runtime = build_runtime()
    assert runtime.registry.get_spec("tool_search").name == "tool_search"
    assert runtime.registry.get_spec("task_verify").name == "task_verify"
    assert runtime.registry.get_spec("task_replan").name == "task_replan"
    assert runtime.registry.get_spec("file_read").name == "file_read"
    assert runtime.session_store is not None
    assert runtime.memory_manager is not None


def test_build_runtime_accepts_workspace_override(tmp_path: Path) -> None:
    runtime = build_runtime(workspace_root=tmp_path)

    file_read = runtime.registry.get_tool("file_read")
    list_dir = runtime.registry.get_tool("list_dir")
    bash = runtime.registry.get_tool("bash")

    assert str(file_read.workspace_root) == str(tmp_path.resolve())
    assert str(list_dir.workspace_root) == str(tmp_path.resolve())
    assert str(bash.session_manager.workspace_root) == str(tmp_path.resolve())
    assert runtime.guardrail_engine.workspace_root == tmp_path.resolve()
    assert runtime.memory_manager.workspace_id == str(tmp_path.resolve())


def test_build_runtime_injects_workspace_override_into_system_prompt(tmp_path: Path) -> None:
    runtime = build_runtime(workspace_root=tmp_path)
    state = AgentState(run_id="r1", user_id="u1", session_id="s1", task="hello")

    prompt = runtime.message_builder.build_system_prompt(state)

    assert f"- Workspace root: {tmp_path.resolve()}" in prompt
    assert "runtime_data\\workspace" not in prompt
    assert "runtime_data/workspace" not in prompt


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
