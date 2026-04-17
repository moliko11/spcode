from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

import examples.example4 as m


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.index = 0
        self.model_name = "fake-llm"

    async def invoke(self, messages):
        if self.index >= len(self.responses):
            raise AssertionError("FakeLLMClient has no more responses")
        resp = self.responses[self.index]
        self.index += 1
        return resp

    def extract_content_and_tool_calls(self, response):
        content = response.content or ""
        tool_calls = []
        for item in getattr(response, "tool_calls", []) or []:
            tool_calls.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "arguments": item.get("args", {}) or {},
                }
            )
        return content, tool_calls


class CountingTool:
    def __init__(self):
        self.counter = 0

    async def arun(self, arguments):
        self.counter += 1
        return f"count={self.counter}"


class FlakyTool:
    def __init__(self):
        self.counter = 0

    async def arun(self, arguments):
        self.counter += 1
        if self.counter == 1:
            raise RuntimeError("temporary failure")
        return "ok"


@pytest.fixture
def temp_runtime_env(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    checkpoints = tmp_path / "checkpoints"
    workspace = tmp_path / "workspace"
    audit_log = tmp_path / "audit.log"
    sessions.mkdir()
    checkpoints.mkdir()
    workspace.mkdir()

    monkeypatch.setattr(m, "SESSION_DIR", sessions)
    monkeypatch.setattr(m, "CHECKPOINT_DIR", checkpoints)
    monkeypatch.setattr(m, "WORKSPACE_DIR", workspace)
    monkeypatch.setattr(m, "AUDIT_LOG_PATH", audit_log)

    return {
        "sessions": sessions,
        "checkpoints": checkpoints,
        "workspace": workspace,
        "audit_log": audit_log,
    }


def build_test_runtime(llm_client, sessions_dir: Path, checkpoints_dir: Path):
    event_bus = m.EventBus()
    event_bus.subscribe(m.AuditSubscriber())
    guardrail = m.GuardrailEngine()
    idempotency_store = m.IdempotencyStore()

    registry = m.ToolRegistry()
    registry.register(
        m.ToolSpec(
            name="calculator",
            description="calculator",
            parameters={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        ),
        m.CalculatorTool(),
    )
    registry.register(
        m.ToolSpec(
            name="write_note",
            description="write note",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            readonly=False,
            risk_level="high",
            side_effect="local_fs",
            sandbox_required=True,
            writes_workspace=True,
            max_retries=0,
            approval_policy="always",
        ),
        m.WriteNoteTool(),
    )
    shell_spec = m.ShellToolSpec(
        name="shell",
        description="shell",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "workdir": {"type": "string"},
            },
            "required": ["command"],
        },
        readonly=False,
        risk_level="high",
        side_effect="shell",
        sandbox_required=True,
        writes_workspace=True,
        cache_policy="none",
        max_retries=0,
        approval_policy="always",
        timeout_s=5.0,
    )
    registry.register(shell_spec, m.ShellTool(m.ShellExecutor(m.WORKSPACE_DIR), lambda: shell_spec))

    role_getter = lambda user_id: "user"

    tool_executor = m.ToolExecutor(
        registry=registry,
        permission_controller=m.PermissionController(role_getter),
        approval_controller=m.ApprovalController(),
        guardrail_engine=guardrail,
        retry_policy=m.RetryPolicy(max_retries=1, base_delay=0.01),
        idempotency_store=idempotency_store,
        event_bus=event_bus,
    )

    runtime = m.AgentRuntime(
        llm_client=llm_client,
        message_builder=m.MessageBuilder(short_memory_turns=6),
        tool_executor=tool_executor,
        registry=registry,
        session_store=m.FileSessionStore(sessions_dir),
        checkpoint_store=m.FileCheckpointStore(checkpoints_dir),
        event_bus=event_bus,
        guardrail_engine=guardrail,
        budget_controller=m.BudgetController(max_steps=8, max_tool_calls=4, max_seconds=30),
        idempotency_store=idempotency_store,
    )
    return runtime


def get_only_run_id(checkpoints_dir: Path) -> str:
    files = list(checkpoints_dir.glob("*.json"))
    assert len(files) == 1
    return files[0].stem


@pytest.mark.asyncio
async def test_normal_tool_call_then_final(temp_runtime_env):
    fake_llm = FakeLLMClient(
        [
            AIMessage(
                content="",
                tool_calls=[{"id": "call_calc_1", "name": "calculator", "args": {"expression": "88+666"}}],
            ),
            AIMessage(content="754"),
        ]
    )

    runtime = build_test_runtime(fake_llm, temp_runtime_env["sessions"], temp_runtime_env["checkpoints"])
    state = await runtime.chat(user_id="u1", session_id="s1", message="88+666=?")

    assert state.status == m.RunStatus.COMPLETED
    assert state.phase == m.Phase.COMPLETED
    assert state.final_output == "754"
    assert len(state.tool_results) == 1
    assert state.tool_results[0].ok is True
    assert state.tool_results[0].output == "754"


@pytest.mark.asyncio
async def test_waiting_human_then_edit_and_approve_resume(temp_runtime_env):
    target_file = temp_runtime_env["workspace"] / "notes" / "today.txt"
    fake_llm = FakeLLMClient(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_write_1",
                        "name": "write_note",
                        "args": {"path": "notes/today.txt", "content": "original"},
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )

    runtime = build_test_runtime(fake_llm, temp_runtime_env["sessions"], temp_runtime_env["checkpoints"])
    state = await runtime.chat(user_id="u1", session_id="s1", message="write note")

    assert state.status == m.RunStatus.WAITING_HUMAN
    assert not target_file.exists()
    assert state.pending_human_request["context"]["tool_name"] == "write_note"

    resumed = await runtime.resume(
        state.run_id,
        human_decision={
            "approved": True,
            "approved_by": "reviewer",
            "edited_arguments": {"path": "notes/today.txt", "content": "edited"},
        },
    )

    assert resumed.status == m.RunStatus.COMPLETED
    assert resumed.final_output == "done"
    assert target_file.read_text(encoding="utf-8") == "edited"
    assert resumed.tool_results[0].approved_by == "reviewer"


@pytest.mark.asyncio
async def test_resume_from_tool_executed_does_not_repeat_tool(temp_runtime_env):
    counting_tool = CountingTool()
    fake_llm = FakeLLMClient(
        [
            AIMessage(content="", tool_calls=[{"id": "call_count_1", "name": "counting_tool", "args": {}}]),
            AIMessage(content="counted"),
        ]
    )

    event_bus = m.EventBus()
    guardrail = m.GuardrailEngine()
    idempotency_store = m.IdempotencyStore()
    registry = m.ToolRegistry()
    registry.register(
        m.ToolSpec(
            name="counting_tool",
            description="counting tool",
            parameters={"type": "object", "properties": {}, "required": []},
            readonly=False,
            cache_policy="success_only",
        ),
        counting_tool,
    )

    tool_executor = m.ToolExecutor(
        registry=registry,
        permission_controller=m.PermissionController(lambda user_id: "user"),
        approval_controller=m.ApprovalController(),
        guardrail_engine=guardrail,
        retry_policy=m.RetryPolicy(max_retries=0, base_delay=0.01),
        idempotency_store=idempotency_store,
        event_bus=event_bus,
    )

    runtime = m.AgentRuntime(
        llm_client=fake_llm,
        message_builder=m.MessageBuilder(short_memory_turns=6),
        tool_executor=tool_executor,
        registry=registry,
        session_store=m.FileSessionStore(temp_runtime_env["sessions"]),
        checkpoint_store=m.FileCheckpointStore(temp_runtime_env["checkpoints"]),
        event_bus=event_bus,
        guardrail_engine=guardrail,
        budget_controller=m.BudgetController(max_steps=8, max_tool_calls=4, max_seconds=30),
        idempotency_store=idempotency_store,
    )

    runtime.set_failpoint("after_tool_executed_checkpoint")
    with pytest.raises(RuntimeError, match="after_tool_executed_checkpoint"):
        await runtime.chat(user_id="u1", session_id="s1", message="run counting tool")

    run_id = get_only_run_id(temp_runtime_env["checkpoints"])
    assert counting_tool.counter == 1

    runtime.set_failpoint(None)
    resumed = await runtime.resume(run_id)

    assert resumed.status == m.RunStatus.COMPLETED
    assert resumed.final_output == "counted"
    assert counting_tool.counter == 1


@pytest.mark.asyncio
async def test_failure_not_cached_when_success_only(temp_runtime_env):
    tool = FlakyTool()
    event_bus = m.EventBus()
    executor = m.ToolExecutor(
        registry=m.ToolRegistry(),
        permission_controller=m.PermissionController(lambda user_id: "user"),
        approval_controller=m.ApprovalController(),
        guardrail_engine=m.GuardrailEngine(),
        retry_policy=m.RetryPolicy(max_retries=0, base_delay=0.01),
        idempotency_store=m.IdempotencyStore(),
        event_bus=event_bus,
    )
    executor.registry.register(
        m.ToolSpec(
            name="flaky",
            description="flaky",
            parameters={"type": "object", "properties": {}, "required": []},
            cache_policy="success_only",
            max_retries=0,
        ),
        tool,
    )

    state = m.AgentState(run_id="r1", user_id="u1", task="t", session_id="s1")
    call = m.ToolCall(call_id="c1", tool_name="flaky", arguments={}, idempotency_key="same")
    first = await executor.execute(state, call)
    second = await executor.execute(state, call)

    assert first.ok is False
    assert second.ok is True
    assert tool.counter == 2


@pytest.mark.asyncio
async def test_shell_tool_requires_approval_and_captures_stdout(temp_runtime_env):
    fake_llm = FakeLLMClient(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_shell_1",
                        "name": "shell",
                        "args": {"command": "Write-Output 'hello shell'"},
                    }
                ],
            ),
            AIMessage(content="shell done"),
        ]
    )

    runtime = build_test_runtime(fake_llm, temp_runtime_env["sessions"], temp_runtime_env["checkpoints"])
    state = await runtime.chat(user_id="u1", session_id="s1", message="run shell")

    assert state.status == m.RunStatus.WAITING_HUMAN
    resumed = await runtime.resume(state.run_id, human_decision={"approved": True})

    assert resumed.status == m.RunStatus.COMPLETED
    assert resumed.tool_results[0].stdout is not None
    assert "hello shell" in resumed.tool_results[0].stdout
    assert resumed.tool_results[0].exit_code == 0


@pytest.mark.asyncio
async def test_shell_denylist_rejected_before_execute(temp_runtime_env):
    event_bus = m.EventBus()
    guardrail = m.GuardrailEngine()
    idempotency_store = m.IdempotencyStore()
    registry = m.ToolRegistry()
    shell_spec = m.ShellToolSpec(
        name="shell",
        description="shell",
        parameters={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        readonly=False,
        risk_level="high",
        side_effect="shell",
        sandbox_required=True,
        writes_workspace=True,
        cache_policy="none",
        max_retries=0,
        approval_policy="always",
        timeout_s=5.0,
    )
    registry.register(shell_spec, m.ShellTool(m.ShellExecutor(m.WORKSPACE_DIR), lambda: shell_spec))
    executor = m.ToolExecutor(
        registry=registry,
        permission_controller=m.PermissionController(lambda user_id: "user"),
        approval_controller=m.ApprovalController(),
        guardrail_engine=guardrail,
        retry_policy=m.RetryPolicy(max_retries=0, base_delay=0.01),
        idempotency_store=idempotency_store,
        event_bus=event_bus,
    )

    result = await executor.execute(
        m.AgentState(run_id="r1", user_id="u1", task="t", session_id="s1"),
        m.ToolCall(
            call_id="c1",
            tool_name="shell",
            arguments={"command": "Remove-Item test.txt"},
            idempotency_key="x",
            metadata={"approved": True},
        ),
    )

    assert result.ok is False
    assert "blocked by pattern" in (result.error or "")


def test_parse_edit_json_requires_json_object():
    payload = m.parse_edit_json('{"path":"hello.py","content":"print(1)\\n"}')
    assert payload["path"] == "hello.py"

    with pytest.raises(ValueError):
        m.parse_edit_json('["not","object"]')


def test_format_pending_human_request_contains_core_fields():
    state = m.AgentState(
        run_id="r1",
        user_id="u1",
        task="t",
        session_id="s1",
        pending_human_request={
            "reason": "tool 'write_note' requires approval",
            "context": {
                "tool_name": "write_note",
                "risk_level": "high",
                "side_effect": "local_fs",
                "arguments": {"path": "hello.py", "content": "print(1)\n"},
            },
            "suggested_actions": ["approve", "reject", "edit_arguments"],
        },
    )

    text = m.format_pending_human_request(state)
    assert "write_note" in text
    assert "high" in text
    assert "hello.py" in text


@pytest.mark.asyncio
async def test_handle_cli_approval_interaction_handles_eof(monkeypatch):
    state = m.AgentState(
        run_id="r1",
        user_id="u1",
        task="t",
        session_id="s1",
        status=m.RunStatus.WAITING_HUMAN,
        pending_human_request={
            "reason": "tool 'write_note' requires approval",
            "context": {
                "tool_name": "write_note",
                "risk_level": "high",
                "side_effect": "local_fs",
                "arguments": {"path": "hello.py"},
            },
            "suggested_actions": ["approve", "reject", "edit_arguments"],
        },
        pending_tool_call={"tool_name": "write_note", "arguments": {"path": "hello.py"}},
    )

    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))

    result = await m.handle_cli_approval_interaction(runtime=None, state=state)
    assert result is state
