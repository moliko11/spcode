from __future__ import annotations

from pathlib import Path

from .config import WORKSPACE_DIR
from .models import GuardrailViolation, ToolResult


def workspace_resolve(path_str: str) -> Path:
    """
    工作空间解析
    """
    base = WORKSPACE_DIR.resolve()
    target = (WORKSPACE_DIR / path_str).resolve()
    if target != base and base not in target.parents:
        raise GuardrailViolation(f"path escapes workspace: {path_str}")
    return target


def truncate_text(text: str | None, limit: int) -> str | None:
    """
    文本截取
    """
    if text is None or len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated]"


class GuardrailEngine:
    """

    """
    def validate_user_input(self, task: str) -> None:
        blocked = ["steal secrets", "delete production database"]
        lower_task = task.lower()
        for pattern in blocked:
            if pattern in lower_task:
                raise GuardrailViolation(f"blocked task pattern detected: {pattern}")

    def validate_tool_args(self, tool_name: str, arguments: dict[str, object]) -> None:
        if not isinstance(arguments, dict):
            raise GuardrailViolation(f"tool '{tool_name}' arguments must be a dict")

        if tool_name == "calculator":
            expression = arguments.get("expression")
            if not isinstance(expression, str) or not expression.strip():
                raise GuardrailViolation("calculator requires a non-empty expression")
        elif tool_name == "file_read":
            self._validate_workspace_path(tool_name, arguments, require_content=False)
        elif tool_name == "file_write":
            self._validate_workspace_path(tool_name, arguments, require_content=True)
            mode = arguments.get("mode", "overwrite")
            if not isinstance(mode, str) or mode not in {"create", "overwrite", "append"}:
                raise GuardrailViolation("file_write.mode must be create, overwrite, or append")
        elif tool_name == "file_edit":
            self._validate_workspace_path(tool_name, arguments, require_content=False)
            action = arguments.get("action")
            if action == "replace_exact":
                if not isinstance(arguments.get("old_str"), str) or not arguments.get("old_str"):
                    raise GuardrailViolation("file_edit.old_str must be a non-empty string")
                if not isinstance(arguments.get("new_str"), str):
                    raise GuardrailViolation("file_edit.new_str must be a string")
            elif action == "insert_at_line":
                if int(arguments.get("line_no", 0)) < 1:
                    raise GuardrailViolation("file_edit.line_no must be >= 1")
                if not isinstance(arguments.get("text"), str):
                    raise GuardrailViolation("file_edit.text must be a string")
            else:
                raise GuardrailViolation("file_edit.action must be replace_exact or insert_at_line")
        elif tool_name == "list_dir":
            path = arguments.get("path", ".")
            if not isinstance(path, str):
                raise GuardrailViolation(f"{tool_name}.path must be a string")
            workspace_resolve(path)
        elif tool_name in {"glob", "grep"}:
            base_path = arguments.get("base_path", ".")
            pattern = arguments.get("pattern")
            if not isinstance(base_path, str):
                raise GuardrailViolation(f"{tool_name}.base_path must be a string")
            if not isinstance(pattern, str) or not pattern.strip():
                raise GuardrailViolation(f"{tool_name}.pattern must be a non-empty string")
            workspace_resolve(base_path)
        elif tool_name == "bash":
            command = arguments.get("command")
            cwd = arguments.get("cwd", ".")
            if not isinstance(command, str) or not command.strip():
                raise GuardrailViolation("bash.command must be a non-empty string")
            if not isinstance(cwd, str):
                raise GuardrailViolation("bash.cwd must be a string")
            workspace_resolve(cwd)
        elif tool_name == "web_search":
            query = arguments.get("query")
            queries = arguments.get("queries")
            if queries is not None:
                if not isinstance(queries, list) or not queries:
                    raise GuardrailViolation("web_search.queries must be a non-empty list")
            elif not isinstance(query, str) or not query.strip():
                raise GuardrailViolation("web_search.query must be a non-empty string")
        elif tool_name == "web_fetch":
            url = arguments.get("url")
            urls = arguments.get("urls")
            if urls is not None:
                if not isinstance(urls, list) or not urls:
                    raise GuardrailViolation("web_fetch.urls must be a non-empty list")
            elif not isinstance(url, str) or not url.strip():
                raise GuardrailViolation("web_fetch.url must be a non-empty string")
        elif tool_name == "tool_search":
            action = arguments.get("action", "search")
            if not isinstance(action, str):
                raise GuardrailViolation("tool_search.action must be a string")
            if action == "search":
                intent = arguments.get("intent", "")
                if not isinstance(intent, str):
                    raise GuardrailViolation("tool_search.intent must be a string")
                keywords = arguments.get("keywords")
                if keywords is not None and not isinstance(keywords, list):
                    raise GuardrailViolation("tool_search.keywords must be a list")
            elif action == "list":
                current_loaded = arguments.get("current_loaded_tools")
                if current_loaded is not None and not isinstance(current_loaded, list):
                    raise GuardrailViolation("tool_search.current_loaded_tools must be a list")
            else:
                raise GuardrailViolation("tool_search.action must be search or list")
        elif tool_name in {"skill", "mcp"}:
            action = arguments.get("action")
            if action is not None and not isinstance(action, str):
                raise GuardrailViolation(f"{tool_name}.action must be a string")

    def validate_tool_result(self, result: ToolResult) -> None:
        if result.stdout and "\0" in result.stdout:
            raise GuardrailViolation("tool stdout contains binary data")
        if result.stderr and "\0" in result.stderr:
            raise GuardrailViolation("tool stderr contains binary data")

    def validate_final_output(self, output: str) -> None:
        if not output.strip():
            raise GuardrailViolation("final output must not be empty")

    def _validate_workspace_path(self, tool_name: str, arguments: dict[str, object], require_content: bool) -> None:
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            raise GuardrailViolation(f"{tool_name}.path must be a non-empty string")
        workspace_resolve(path)
        if require_content and not isinstance(arguments.get("content"), str):
            raise GuardrailViolation(f"{tool_name}.content must be a string")
