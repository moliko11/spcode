from __future__ import annotations

from packages.runtime.agent_loop import AgentRuntime
from packages.runtime.autonomy import AutonomyPolicy
from packages.runtime.message_builder import MessageBuilder
from packages.runtime.models import AgentState, SessionMessage


def test_autonomy_policy_detects_plan_only_request() -> None:
    decision = AutonomyPolicy().decide("先规划一下，不要执行")

    assert decision.mode == "plan_only"
    assert decision.should_use_todo is True
    assert decision.plan_mode is not None
    assert decision.plan_mode["active"] is True
    assert decision.plan_mode["requires_user_approval"] is True


def test_autonomy_policy_detects_approval_of_previous_plan() -> None:
    conversation = [SessionMessage(role="assistant", content="这是计划，请确认是否执行。")]

    decision = AutonomyPolicy().decide("可以", conversation)

    assert decision.mode == "approve_previous_plan"
    assert decision.should_verify is True
    assert decision.should_replan_on_failure is True


def test_autonomy_policy_detects_complex_actionable_request() -> None:
    decision = AutonomyPolicy().decide("实现普通 chat 自主规划执行，并补充测试")

    assert decision.mode == "autonomous"
    assert decision.should_use_todo is True
    assert decision.should_verify is True


def test_message_builder_injects_autonomy_policy_text() -> None:
    state = AgentState(run_id="r1", user_id="u1", session_id="s1", task="实现普通 chat 自主规划执行，并补充测试")
    decision = AutonomyPolicy().decide(state.task)
    state.metadata["autonomy_policy"] = decision.to_dict()

    prompt = MessageBuilder().build_system_prompt(state)

    assert "Autonomous workflow policy" in prompt
    assert "Mode: autonomous" in prompt
    assert "todo_write" in prompt
    assert "task_verify" in prompt


def test_agent_runtime_applies_autonomy_policy_to_state() -> None:
    runtime = object.__new__(AgentRuntime)
    runtime.autonomy_policy = AutonomyPolicy()
    state = AgentState(
        run_id="r1",
        user_id="u1",
        session_id="s1",
        task="只规划一下，不要执行",
        conversation=[SessionMessage(role="user", content="只规划一下，不要执行")],
    )

    runtime._apply_autonomy_policy(state)

    assert state.metadata["autonomy_policy"]["mode"] == "plan_only"
    assert state.metadata["plan_mode"]["active"] is True
