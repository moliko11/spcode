from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .config import CURRENT_DATE, CURRENT_TIMEZONE, DEFAULT_LOADED_TOOL_NAMES, DYNAMIC_TOOL_NAMES, SHORT_MEMORY_TURNS, WORKSPACE_DIR
from .models import AgentState
from .prompts import PromptSelector

if TYPE_CHECKING:
    from packages.tools.SkillTool.tool import SkillTool


class MessageBuilder:
    """根据 AgentState 构建 LLM 输入消息列表，包括 system prompt 和近期对话历史。
    system prompt 由 PromptSelector 组装，包含 base + specialist overlay，以及工具列表和自治政策等动态信息。
    """
    def __init__(
        self,
        short_memory_turns: int = SHORT_MEMORY_TURNS,
        default_loaded_tool_names: list[str] | None = None,
        skill_tool: "SkillTool | None" = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        self.short_memory_turns = short_memory_turns
        self.default_loaded_tool_names = list(default_loaded_tool_names or DEFAULT_LOADED_TOOL_NAMES)
        self.skill_tool = skill_tool
        self.workspace_root = Path(workspace_root).resolve() if workspace_root is not None else WORKSPACE_DIR.resolve()
        self._selector = PromptSelector(
            workspace_root=str(self.workspace_root),
            skill_tool=skill_tool,
        )

    def build_system_prompt(self, state: AgentState) -> str:
        loaded_tools = state.metadata.get("loaded_tools")
        if not loaded_tools:
            # 兜底覆盖三种情况：键缺失（老 checkpoint）、显式 None、空 list
            loaded_tools = self.default_loaded_tool_names
        loaded = ", ".join(str(name) for name in loaded_tools)
        dynamic = ", ".join(DYNAMIC_TOOL_NAMES)
        current_dt = self._current_datetime_text()
        autonomy_text = self._autonomy_policy_text(state)
        return self._selector.assemble(
            state,
            datetime_dict={**current_dt, "timezone": CURRENT_TIMEZONE},
            loaded_tools=loaded,
            dynamic_tools=dynamic,
            autonomy_text=autonomy_text,
        )

    def _autonomy_policy_text(self, state: AgentState) -> str:
        policy = state.metadata.get("autonomy_policy")
        if not isinstance(policy, dict):
            return ""
        mode = str(policy.get("mode") or "direct")
        lines = ["Autonomous workflow policy for this user message:", f"- Mode: {mode}", f"- Reason: {policy.get('reason') or ''}"]
        if policy.get("should_use_todo"):
            lines.append("- Use todo_write early to keep a visible execution plan current.")
        if policy.get("should_verify"):
            lines.append("- Verify completed work using tests, inspection, task_verify, or concrete evidence before finalizing.")
        if policy.get("should_replan_on_failure"):
            lines.append("- If verification or execution fails, use task_replan or update task state before retrying.")
        instructions = policy.get("instructions")
        if isinstance(instructions, list):
            for item in instructions:
                if isinstance(item, str) and item.strip():
                    lines.append(f"- {item.strip()}")
        plan_mode = state.metadata.get("plan_mode")
        if isinstance(plan_mode, dict) and plan_mode.get("active"):
            lines.append("- Plan mode is currently active: do not use write, shell, network, or external side-effect tools.")
        return "\n".join(lines)

    def _current_datetime_text(self) -> dict[str, str]:
        date_text = CURRENT_DATE or datetime.date.today().isoformat()
        try:
            tz = ZoneInfo(CURRENT_TIMEZONE)
            now = datetime.datetime.now(tz)
            if CURRENT_DATE:
                y, m, d = (int(part) for part in CURRENT_DATE.split("-"))
                now = now.replace(year=y, month=m, day=d)
            datetime_text = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        except (ValueError, ZoneInfoNotFoundError):
            datetime_text = f"{date_text} 00:00:00 {CURRENT_TIMEZONE}"
        return {"date": date_text, "datetime": datetime_text}

    def build_initial_messages(self, state: AgentState) -> list[Any]:
        recent = state.conversation[-self.short_memory_turns :]
        messages: list[Any] = [SystemMessage(content=self.build_system_prompt(state))]
        for msg in recent:
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                messages.append(AIMessage(content=msg.content))
        return messages
