from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .models import SessionMessage


@dataclass(slots=True)
class AutonomyDecision:
    mode: str
    reason: str
    should_use_todo: bool = False
    should_verify: bool = False
    should_replan_on_failure: bool = False
    plan_mode: dict[str, Any] | None = None
    instructions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "should_use_todo": self.should_use_todo,
            "should_verify": self.should_verify,
            "should_replan_on_failure": self.should_replan_on_failure,
            "plan_mode": self.plan_mode,
            "instructions": list(self.instructions),
        }


class AutonomyPolicy:
    PLAN_ONLY_PATTERNS = (
        r"只规划",
        r"仅规划",
        r"先规划",
        r"不要执行",
        r"先别(执行|修改|改|动)",
        r"别(执行|修改|改|动)",
        r"plan only",
        r"planning only",
    )
    APPROVAL_PATTERNS = (
        r"^可以$",
        r"^继续$",
        r"^执行$",
        r"^开始吧$",
        r"^按这个(做|执行)$",
        r"approved?",
        r"go ahead",
    )
    ACTION_PATTERNS = (
        r"实现",
        r"修复",
        r"修改",
        r"完善",
        r"补充",
        r"接入",
        r"重构",
        r"添加",
        r"删除",
        r"提交",
        r"测试",
        r"验证",
        r"implement",
        r"fix",
        r"update",
        r"refactor",
        r"add",
        r"commit",
        r"test",
    )

    def decide(self, message: str, conversation: list[SessionMessage] | None = None) -> AutonomyDecision:
        text = message.strip()
        lowered = text.lower()
        conversation = conversation or []

        if self._matches(text, self.PLAN_ONLY_PATTERNS):
            plan_mode = {
                "active": True,
                "plan_mode_session_id": f"planmode_{uuid.uuid4().hex[:10]}",
                "goal": text,
                "reason": "user requested planning without execution",
                "constraints": ["no side-effect tools until the user approves execution"],
                "requires_user_approval": True,
                "entered_at": time.time(),
            }
            return AutonomyDecision(
                mode="plan_only",
                reason="explicit plan-only request",
                should_use_todo=True,
                should_verify=False,
                should_replan_on_failure=False,
                plan_mode=plan_mode,
                instructions=[
                    "Start from plan mode and do not perform writes, shell commands, network calls, or other side-effect work.",
                    "Use todo_write or task tools to present the plan when useful.",
                    "Finish by asking for approval before execution.",
                ],
            )

        if self._matches(lowered, self.APPROVAL_PATTERNS) and self._previous_assistant_asked_for_approval(conversation):
            return AutonomyDecision(
                mode="approve_previous_plan",
                reason="user approved the previous proposed plan",
                should_use_todo=True,
                should_verify=True,
                should_replan_on_failure=True,
                instructions=[
                    "Treat this as approval to execute the previous plan in the conversation.",
                    "Use todo_write to track execution if the task has multiple steps.",
                    "Verify completed work with tests, inspection, task_verify, or concrete evidence before finalizing.",
                ],
            )

        complex_task = self._is_complex_task(text)
        if complex_task:
            return AutonomyDecision(
                mode="autonomous",
                reason="multi-step actionable request",
                should_use_todo=True,
                should_verify=True,
                should_replan_on_failure=True,
                instructions=[
                    "Use todo_write early to expose a short execution plan for multi-step work.",
                    "Execute the useful next action without waiting when the request is clear.",
                    "After implementation, verify with tests, inspection, task_verify, or other concrete evidence.",
                    "If verification fails, use task_replan or update the todo list before retrying.",
                ],
            )

        return AutonomyDecision(
            mode="direct",
            reason="simple conversational or informational request",
            instructions=["Answer directly unless a tool is needed for accuracy."],
        )

    def _is_complex_task(self, text: str) -> bool:
        lowered = text.lower()
        action_hits = sum(1 for pattern in self.ACTION_PATTERNS if re.search(pattern, lowered, flags=re.IGNORECASE))
        if action_hits >= 1 and len(text) >= 8:
            return True
        if len(text) >= 80 and any(mark in text for mark in ("，", ",", "；", ";", "然后", "并且", "同时")):
            return True
        return False

    def _previous_assistant_asked_for_approval(self, conversation: list[SessionMessage]) -> bool:
        for message in reversed(conversation[-6:]):
            if message.role != "assistant":
                continue
            content = message.content.lower()
            return any(token in content for token in ("批准", "approval", "确认", "继续执行", "是否执行", "开始执行"))
        return False

    def _matches(self, text: str, patterns: tuple[str, ...]) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
