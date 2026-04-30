from __future__ import annotations

import re
from typing import Callable

from .models import AgentState, HumanInterventionRequest, HumanInterventionRequired, PermissionDenied, ToolCall, ToolSpec, to_jsonable


class PermissionController:
    """
    权限控制器
    """
    def __init__(self, role_getter: Callable[[str], str]) -> None:
        self._role_getter = role_getter

    def check_tool_permission(self, state: AgentState, spec: ToolSpec) -> None:
        role = self._role_getter(state.user_id)
        if spec.allowed_roles and role not in spec.allowed_roles:
            raise PermissionDenied(f"role '{role}' cannot access tool '{spec.name}'")


class ApprovalController:
    """
    审批控制器
    """
    def needs_approval(self, spec: ToolSpec, call: ToolCall) -> bool:
        if call.metadata.get("approved"):
            return False
        if spec.name == "bash" and self._is_safe_readonly_bash(call):
            return False
        if spec.approval_policy == "never":
            return False
        if spec.approval_policy == "always":
            return True
        if spec.approval_policy == "on_write":
            return spec.writes_workspace
        if spec.approval_policy == "on_high_risk":
            return spec.risk_level in {"high", "critical"}
        raise ValueError(f"unsupported approval policy: {spec.approval_policy}")

    def _is_safe_readonly_bash(self, call: ToolCall) -> bool:
        command = str(call.arguments.get("command", "")).strip()
        if not command:
            return False
        # Keep this narrow: trusted local 12306 scripts only. A single leading
        # cd into the skill directory is allowed because models often emit it.
        if re.search(r"[|><`\r\n]", command):
            return False
        if re.search(r"(^|\s)(-o|--output)(\s|=)", command):
            return False
        normalized = command.replace("\\", "/")
        direct = r'^node\s+"?[A-Za-z]:?/?.*?/skills/12306-skill/scripts/(stations|query)\.mjs"?(\s|$)'
        cd_then_node = r'^cd\s+"?[A-Za-z]:?/?.*?/skills/12306-skill"?\s*(;|&&)\s*node\s+scripts/(stations|query)\.mjs(\s|$)'
        return bool(re.match(direct, normalized) or re.match(cd_then_node, normalized))

    async def require_approval_if_needed(self, spec: ToolSpec, call: ToolCall) -> None:
        if not self.needs_approval(spec, call):
            return
        raise HumanInterventionRequired(
            HumanInterventionRequest(
                reason=f"tool '{spec.name}' requires approval",
                context={
                    "tool_name": spec.name,
                    "arguments": to_jsonable(call.arguments),
                    "risk_level": spec.risk_level,
                    "side_effect": spec.side_effect,
                    "reason": f"approval_policy={spec.approval_policy}",
                },
                suggested_actions=["approve", "reject", "edit_arguments"],
            )
        )
