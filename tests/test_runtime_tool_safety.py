from __future__ import annotations

from packages.runtime.agent_loop import AgentRuntime
from packages.runtime.models import AgentState, ToolCall, ToolResult, ToolSpec
from packages.runtime.permission import ApprovalController


def test_approval_skips_trusted_12306_node_scripts() -> None:
    controller = ApprovalController()
    spec = ToolSpec(
        name="bash",
        description="shell",
        parameters={},
        approval_policy="always",
        risk_level="high",
        side_effect="shell",
    )
    call = ToolCall(
        call_id="c1",
        tool_name="bash",
        arguments={
            "command": 'node "H:/repo/skills/12306-skill/scripts/query.mjs" 郑州 驻马店 -d 2026-04-30 -f md'
        },
        idempotency_key="k1",
    )

    assert controller.needs_approval(spec, call) is False


def test_approval_skips_trusted_12306_cd_then_node() -> None:
    controller = ApprovalController()
    spec = ToolSpec(
        name="bash",
        description="shell",
        parameters={},
        approval_policy="always",
        risk_level="high",
        side_effect="shell",
    )
    call = ToolCall(
        call_id="c1",
        tool_name="bash",
        arguments={
            "command": "cd H:\\repo\\skills\\12306-skill && node scripts/query.mjs 郑州 驻马店 -d 2026-04-30 -f md"
        },
        idempotency_key="k1",
    )

    assert controller.needs_approval(spec, call) is False


def test_approval_keeps_general_bash_under_human_review() -> None:
    controller = ApprovalController()
    spec = ToolSpec(
        name="bash",
        description="shell",
        parameters={},
        approval_policy="always",
        risk_level="high",
        side_effect="shell",
    )
    call = ToolCall(
        call_id="c1",
        tool_name="bash",
        arguments={"command": "Get-ChildItem"},
        idempotency_key="k1",
    )

    assert controller.needs_approval(spec, call) is True


def test_approval_reviews_12306_custom_output_path() -> None:
    controller = ApprovalController()
    spec = ToolSpec(
        name="bash",
        description="shell",
        parameters={},
        approval_policy="always",
        risk_level="high",
        side_effect="shell",
    )
    call = ToolCall(
        call_id="c1",
        tool_name="bash",
        arguments={
            "command": 'node "H:/repo/skills/12306-skill/scripts/query.mjs" 郑州 驻马店 -d 2026-04-30 -o H:/tmp/out.html'
        },
        idempotency_key="k1",
    )

    assert controller.needs_approval(spec, call) is True


def test_runtime_finds_reusable_result_by_tool_arguments() -> None:
    runtime = object.__new__(AgentRuntime)
    state = AgentState(
        run_id="r1",
        user_id="u1",
        task="t",
        session_id="s1",
        tool_results=[
            ToolResult(
                call_id="old",
                tool_name="web_search",
                ok=True,
                output="cached weather",
                metadata={"arguments": {"query": "郑州 天气"}},
            )
        ],
    )

    found = runtime._find_reusable_result(
        state,
        {"id": "new", "name": "web_search", "arguments": {"query": "郑州 天气"}},
    )

    assert found is state.tool_results[0]
