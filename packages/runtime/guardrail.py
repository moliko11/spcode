from __future__ import annotations

from pathlib import Path

from .config import SKILL_ROOTS, WORKSPACE_DIR
from .models import GuardrailViolation, ToolResult


def workspace_resolve(path_str: str) -> Path:
    """
    工作空间解析（写操作用）：仅允许 WORKSPACE_DIR 下的路径
    """
    base = WORKSPACE_DIR.resolve()
    target = (WORKSPACE_DIR / path_str).resolve()
    if target != base and base not in target.parents:
        raise GuardrailViolation(f"path escapes workspace: {path_str}")
    return target


def read_resolve(path_str: str) -> Path:
    """
    只读路径解析：允许 WORKSPACE_DIR 或任意 SKILL_ROOTS 下的路径
    """
    # 先尝试绝对路径
    candidate = Path(path_str)
    if candidate.is_absolute():
        target = candidate.resolve()
    else:
        target = (WORKSPACE_DIR / path_str).resolve()

    allowed_bases = [WORKSPACE_DIR.resolve()] + [r.resolve() for r in SKILL_ROOTS]
    for base in allowed_bases:
        if target == base or base in target.parents:
            return target
    raise GuardrailViolation(f"path escapes workspace: {path_str}")


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
            self._validate_read_path(tool_name, arguments)
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
        elif tool_name in {"task_create", "task_update", "task_list", "task_output", "task_stop"}:
            self._validate_task_tool_args(tool_name, arguments)

    def validate_tool_result(self, result: ToolResult) -> None:
        if result.stdout and "\0" in result.stdout:
            raise GuardrailViolation("tool stdout contains binary data")
        if result.stderr and "\0" in result.stderr:
            raise GuardrailViolation("tool stderr contains binary data")

    def validate_final_output(self, output: str) -> None:
        if not output.strip():
            raise GuardrailViolation("final output must not be empty")

    def _validate_read_path(self, tool_name: str, arguments: dict[str, object]) -> None:
        """只读路径校验：允许 WORKSPACE_DIR 或 SKILL_ROOTS 下的路径"""
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            raise GuardrailViolation(f"{tool_name}.path must be a non-empty string")
        read_resolve(path)

    def _validate_workspace_path(self, tool_name: str, arguments: dict[str, object], require_content: bool) -> None:
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            raise GuardrailViolation(f"{tool_name}.path must be a non-empty string")
        workspace_resolve(path)
        if require_content and not isinstance(arguments.get("content"), str):
            raise GuardrailViolation(f"{tool_name}.content must be a string")

    def _validate_task_tool_args(self, tool_name: str, arguments: dict[str, object]) -> None:
        for key in ("plan_id", "plan_run_id", "task_id", "status", "title", "description", "reason"):
            value = arguments.get(key)
            if value is not None and not isinstance(value, str):
                raise GuardrailViolation(f"{tool_name}.{key} must be a string")
        for key in ("dependencies", "acceptance_criteria", "suggested_tools", "target_files", "artifacts", "evidence"):
            value = arguments.get(key)
            if value is not None and not isinstance(value, list):
                raise GuardrailViolation(f"{tool_name}.{key} must be a list")
        if tool_name == "task_create":
            title = arguments.get("title")
            if not isinstance(title, str) or not title.strip():
                raise GuardrailViolation("task_create.title must be a non-empty string")
        if tool_name == "task_update":
            task_id = arguments.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                raise GuardrailViolation("task_update.task_id must be a non-empty string")
            status = arguments.get("status")
            allowed = {"pending", "ready", "running", "waiting_human", "completed", "failed", "skipped", "blocked", "cancelled"}
            if status is not None and status not in allowed:
                raise GuardrailViolation(f"task_update.status must be one of: {', '.join(sorted(allowed))}")
        if tool_name in {"task_list", "task_output", "task_stop"}:
            limit = arguments.get("limit")
            if limit is not None and (not isinstance(limit, int) or limit < 1):
                raise GuardrailViolation(f"{tool_name}.limit must be a positive integer")
