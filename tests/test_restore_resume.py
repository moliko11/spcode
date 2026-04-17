from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

import examples.example3 as m


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.index = 0
        self.model_name = "fake-llm"

    async def invoke(self, messages):
        if self.index >= len(self.responses):
            raise AssertionError("FakeLLMClient 没有更多响应了")
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


@pytest.fixture
def temp_runtime_env(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    checkpoints = tmp_path / "checkpoints"
    workspace = tmp_path / "workspace"
    sessions.mkdir()
    checkpoints.mkdir()
    workspace.mkdir()

    monkeypatch.setattr(m, "SESSION_DIR", sessions)
    monkeypatch.setattr(m, "CHECKPOINT_DIR", checkpoints)
    monkeypatch.setattr(m, "WORKSPACE_DIR", workspace)

    return {
        "sessions": sessions,
        "checkpoints": checkpoints,
        "workspace": workspace,
    }


def build_test_runtime(
    llm_client,
    sessions_dir: Path,
    checkpoints_dir: Path,
):
    event_bus = m.EventBus()
    guardrail = m.GuardrailEngine()
    idempotency_store = m.IdempotencyStore()

    registry = m.ToolRegistry()
    registry.register(
        m.ToolSpec(
            name="calculator",
            description="计算器",
            parameters={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
            readonly=True,
        ),
        m.CalculatorTool(),
    )
    registry.register(
        m.ToolSpec(
            name="get_current_time",
            description="获取时间",
            parameters={"type": "object", "properties": {}, "required": []},
            readonly=True,
        ),
        m.GetCurrentTimeTool(),
    )
    registry.register(
        m.ToolSpec(
            name="read_file",
            description="读取文件",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            readonly=True,
        ),
        m.ReadFileTool(),
    )
    registry.register(
        m.ToolSpec(
            name="write_note",
            description="写文件",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            require_approval=True,
            readonly=False,
        ),
        m.WriteNoteTool(),
    )

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
        budget_controller=m.BudgetController(
            max_steps=8,
            max_tool_calls=4,
            max_seconds=30,
        ),
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
                tool_calls=[
                    {
                        "id": "call_calc_1",
                        "name": "calculator",
                        "args": {"expression": "88+666"},
                    }
                ],
            ),
            AIMessage(content="88+666等于754。"),
        ]
    )

    runtime = build_test_runtime(
        llm_client=fake_llm,
        sessions_dir=temp_runtime_env["sessions"],
        checkpoints_dir=temp_runtime_env["checkpoints"],
    )

    state = await runtime.chat(
        user_id="u1",
        session_id="s1",
        message="88+666是多少",
    )

    assert state.status == m.RunStatus.COMPLETED
    assert state.phase == m.Phase.COMPLETED
    assert state.final_output == "88+666等于754。"
    assert len(state.tool_results) == 1
    assert state.tool_results[0].ok is True
    assert state.tool_results[0].output == "754"


@pytest.mark.asyncio
async def test_waiting_human_then_approve_resume(temp_runtime_env):
    target_file = temp_runtime_env["workspace"] / "notes" / "today.txt"

    fake_llm = FakeLLMClient(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_write_1",
                        "name": "write_note",
                        "args": {
                            "path": "notes/today.txt",
                            "content": "今天完成了 restore resume MVP",
                        },
                    }
                ],
            ),
            AIMessage(content="已经写好了。"),
        ]
    )

    runtime = build_test_runtime(
        llm_client=fake_llm,
        sessions_dir=temp_runtime_env["sessions"],
        checkpoints_dir=temp_runtime_env["checkpoints"],
    )

    state = await runtime.chat(
        user_id="u1",
        session_id="s1",
        message="请帮我把今天的想法写到 notes/today.txt",
    )

    assert state.status == m.RunStatus.WAITING_HUMAN
    assert state.phase == m.Phase.WAITING_HUMAN
    assert not target_file.exists()

    resumed = await runtime.resume(
        state.run_id,
        human_decision={"approved": True},
    )

    assert resumed.status == m.RunStatus.COMPLETED
    assert resumed.phase == m.Phase.COMPLETED
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "今天完成了 restore resume MVP"
    assert resumed.final_output == "已经写好了。"


@pytest.mark.asyncio
async def test_waiting_human_then_reject(temp_runtime_env):
    target_file = temp_runtime_env["workspace"] / "notes" / "reject.txt"

    fake_llm = FakeLLMClient(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_write_2",
                        "name": "write_note",
                        "args": {
                            "path": "notes/reject.txt",
                            "content": "这段内容不该被写入",
                        },
                    }
                ],
            ),
        ]
    )

    runtime = build_test_runtime(
        llm_client=fake_llm,
        sessions_dir=temp_runtime_env["sessions"],
        checkpoints_dir=temp_runtime_env["checkpoints"],
    )

    state = await runtime.chat(
        user_id="u1",
        session_id="s1",
        message="请写入 notes/reject.txt",
    )

    assert state.status == m.RunStatus.WAITING_HUMAN
    assert state.phase == m.Phase.WAITING_HUMAN

    resumed = await runtime.resume(
        state.run_id,
        human_decision={"approved": False},
    )

    assert resumed.status == m.RunStatus.COMPLETED
    assert resumed.phase == m.Phase.COMPLETED
    assert resumed.final_output == "人工审批已拒绝，本次执行终止。"
    assert not target_file.exists()


@pytest.mark.asyncio
async def test_resume_from_tool_executed_does_not_repeat_tool(temp_runtime_env):
    counting_tool = CountingTool()

    fake_llm = FakeLLMClient(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_count_1",
                        "name": "counting_tool",
                        "args": {},
                    }
                ],
            ),
            AIMessage(content="计数工具已经执行完毕。"),
        ]
    )

    event_bus = m.EventBus()
    guardrail = m.GuardrailEngine()
    idempotency_store = m.IdempotencyStore()

    registry = m.ToolRegistry()
    registry.register(
        m.ToolSpec(
            name="counting_tool",
            description="测试用计数工具",
            parameters={"type": "object", "properties": {}, "required": []},
            readonly=False,
        ),
        counting_tool,
    )

    role_getter = lambda user_id: "user"

    tool_executor = m.ToolExecutor(
        registry=registry,
        permission_controller=m.PermissionController(role_getter),
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
        budget_controller=m.BudgetController(
            max_steps=8,
            max_tool_calls=4,
            max_seconds=30,
        ),
        idempotency_store=idempotency_store,
    )

    runtime.set_failpoint("after_tool_executed_checkpoint")

    with pytest.raises(RuntimeError, match="after_tool_executed_checkpoint"):
        await runtime.chat(
            user_id="u1",
            session_id="s1",
            message="执行 counting_tool",
        )

    run_id = get_only_run_id(temp_runtime_env["checkpoints"])

    # 关键断言：第一次已经真正执行过一次
    assert counting_tool.counter == 1

    # 清掉 failpoint，恢复继续跑
    runtime.set_failpoint(None)
    resumed = await runtime.resume(run_id)

    assert resumed.status == m.RunStatus.COMPLETED
    assert resumed.phase == m.Phase.COMPLETED
    assert resumed.final_output == "计数工具已经执行完毕。"

    # 关键断言：恢复后没有重复执行
    assert counting_tool.counter == 1