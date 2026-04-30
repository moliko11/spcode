from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .config import CURRENT_DATE, CURRENT_TIMEZONE, DEFAULT_LOADED_TOOL_NAMES, DYNAMIC_TOOL_NAMES, SHORT_MEMORY_TURNS, WORKSPACE_DIR
from .models import AgentState

if TYPE_CHECKING:
    from packages.tools.SkillTool.tool import SkillTool


class MessageBuilder:
    def __init__(
        self,
        short_memory_turns: int = SHORT_MEMORY_TURNS,
        skill_tool: "SkillTool | None" = None,
    ) -> None:
        self.short_memory_turns = short_memory_turns
        self.skill_tool = skill_tool

    def build_system_prompt(self, state: AgentState) -> str:
        loaded_tools = state.metadata.get("loaded_tools", DEFAULT_LOADED_TOOL_NAMES)
        loaded = ", ".join(str(name) for name in loaded_tools)
        dynamic = ", ".join(DYNAMIC_TOOL_NAMES)
        current_dt = self._current_datetime_text()
        prompt = (
            "You are an agentic coding assistant operating inside a local workspace.\n\n"
            "Current environment:\n"
            f"- Current date: {current_dt['date']}\n"
            f"- Current local time: {current_dt['datetime']}\n"
            f"- Timezone: {CURRENT_TIMEZONE}\n"
            f"- Workspace root: {WORKSPACE_DIR.resolve()}\n\n"
            "Identity:\n"
            "- You can inspect files, search code, modify files, fetch web content, search the web, inspect skills, inspect MCP configuration, and run shell commands when needed.\n"
            "- You are precise, tool-aware, and action-oriented.\n\n"
            "Core behavior:\n"
            "- Prefer concrete actions over speculative answers.\n"
            "- Use tools when they improve accuracy or are required to complete the task.\n"
            "- Never fabricate file contents, command results, tool outputs, or web results.\n"
            "- Base each next action on previous tool observations.\n"
            "- Prefer specialized tools over shell commands.\n\n"
            "Tool policy:\n"
            "- Use `glob` to discover files by path pattern.\n"
            "- Use `grep` to search code or text content.\n"
            "- Use `file_read` to inspect files.\n"
            "- Use `file_edit` for targeted modifications.\n"
            "- Use `file_write` to create, overwrite, or append files.\n"
            "- Use `web_search` to discover candidate web sources. Leave page content disabled unless snippets are insufficient, then fetch only the best source with `web_fetch`.\n"
            "- Use `web_fetch` to inspect specific URLs.\n"
            "- Use `bash` only when specialized tools are insufficient.\n"
            "- For `bash`, prefer the `cwd` argument over `cd ...` in the command. The shell is PowerShell on Windows; do not use POSIX-only syntax.\n"
            "- Use `tool_search` if the currently loaded toolset appears insufficient.\n"
            "- Use task tools only when the user asks for persisted task tracking or the work truly needs a managed multi-step plan.\n"
            "- Use `skill` to load and invoke a skill when relevant.\n"
            "- Use `mcp` only after discovering a relevant MCP capability.\n\n"
            "Dynamic tools:\n"
            f"- Default loaded tools in this run: {loaded}\n"
            f"- Additional tools may exist but are not loaded by default: {dynamic}\n"
            "- Do not assume a hidden tool is available unless it is discovered through `tool_search` and then loaded.\n\n"
            "Decision style:\n"
            "- First determine whether the task is best handled by direct answer, workspace inspection, file modification, web research, or tool discovery.\n"
            "- For coding tasks, prefer local workspace tools first.\n"
            "- For multi-step tasks, create or list workflow tasks first, keep task status current, and do not mark a task completed until there is concrete evidence.\n"
            "- For external or time-sensitive facts, prefer web tools first.\n"
            "- If the task is ambiguous, choose the smallest useful next action.\n\n"
            "Date and tool-call discipline:\n"
            "- Resolve relative dates from the current local date before calling tools. Chinese date words: today=今天, tomorrow=明天, yesterday=昨天.\n"
            "- When a tool supports date options, pass dates through the documented date option instead of as extra positional text.\n"
            "- Avoid repeating the same tool call with the same arguments after a successful observation; use the existing observation to answer or refine with different arguments.\n\n"
            "Response style:\n"
            "- Be concise.\n"
            "- Do not expose hidden chain-of-thought.\n"
            "- Summarize results based on observations.\n"
            "- State uncertainty when evidence is incomplete.\n"
        )
        # Inject skill listing so LLM knows available skills without needing to call list
        if self.skill_tool is not None:
            listing = self.skill_tool.build_skill_listing()
            if listing:
                prompt += "\n\n" + listing + "\n"
        recall_text = state.metadata.get("recall_text")
        if recall_text:
            prompt += "\n" + recall_text + "\n"
        return prompt

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
