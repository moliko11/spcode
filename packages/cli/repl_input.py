from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SlashCommandSpec:
    name: str
    description: str
    usage: str
    aliases: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        return f"/{self.name}"


SLASH_COMMANDS: tuple[SlashCommandSpec, ...] = (
    SlashCommandSpec("help", "显示工作台命令", "/help"),
    SlashCommandSpec("history", "显示当前 session 历史", "/history [limit]", aliases=("hist",)),
    SlashCommandSpec("plan", "生成计划但不执行", "/plan <goal>", aliases=("p",)),
    SlashCommandSpec("run", "生成计划并执行", "/run <goal>", aliases=("orchestrate",)),
    SlashCommandSpec("plans", "列出最近计划", "/plans"),
    SlashCommandSpec("runs", "列出最近 plan run", "/runs"),
    SlashCommandSpec("approvals", "列出等待审批的 plan run", "/approvals", aliases=("approval",)),
    SlashCommandSpec("approve", "审批并恢复等待中的 plan run", "/approve <plan_run_id>"),
    SlashCommandSpec("reject", "拒绝等待中的 plan run", "/reject <plan_run_id>"),
    SlashCommandSpec("recover", "恢复未完成的 plan run", "/recover <plan_run_id>"),
    SlashCommandSpec("memory", "列出近期记忆", "/memory", aliases=("mem",)),
    SlashCommandSpec("model", "查看或切换 provider", "/model [provider]"),
    SlashCommandSpec("clear", "开启一个新 session", "/clear"),
    SlashCommandSpec("status", "显示当前工作台状态", "/status"),
    SlashCommandSpec("quit", "退出", "/quit", aliases=("exit", "q")),
)


def iter_slash_command_names(include_aliases: bool = True) -> list[str]:
    names: list[str] = []
    for command in SLASH_COMMANDS:
        names.append(f"/{command.name}")
        if include_aliases:
            names.extend(f"/{alias}" for alias in command.aliases)
    return names


def slash_command_help_rows() -> list[tuple[str, str]]:
    return [(command.usage, command.description) for command in SLASH_COMMANDS]


def create_prompt_session() -> Any | None:
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.history import InMemoryHistory
    except Exception:
        return None

    class SlashCommandCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            token = text.split(maxsplit=1)[0]
            for command in SLASH_COMMANDS:
                candidates = [(command.name, command.description), *[(alias, f"alias for /{command.name}") for alias in command.aliases]]
                for name, description in candidates:
                    value = f"/{name}"
                    if value.startswith(token):
                        yield Completion(
                            value,
                            start_position=-len(token),
                            display=value,
                            display_meta=description,
                        )

    return PromptSession(
        history=InMemoryHistory(),
        completer=SlashCommandCompleter(),
        complete_while_typing=True,
    )


def read_repl_input(console: Any, prompt_session: Any | None) -> str:
    if prompt_session is None:
        return console.input("[bold green]agent>[/] ")
    return prompt_session.prompt("agent> ")
