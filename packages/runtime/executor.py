from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .budget import IdempotencyStore, RetryPolicy, diff_workspace, snapshot_workspace
from .config import WORKSPACE_DIR
from .events import EventBus
from .guardrail import GuardrailEngine, truncate_text, workspace_resolve
from .models import (
    AgentEvent,
    AgentState,
    EventType,
    GuardrailViolation,
    HumanInterventionRequired,
    PermissionDenied,
    ShellToolSpec,
    ToolCall,
    ToolResult,
)
from .permission import ApprovalController, PermissionController
from .registry import ToolRegistry


class ShellExecutor:
    """
    Shell工具执行器
    """
    def __init__(self, workspace_dir: Path = WORKSPACE_DIR) -> None:
        self.workspace_dir = workspace_dir

    async def run(
        self,
        spec: ShellToolSpec,
        arguments: dict[str, Any],
        *,
        on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> ToolResult:
        command = arguments["command"]
        workdir = self._resolve_workdir(spec, arguments.get("workdir", "."))
        self._check_command_allowed(spec, command)
        before = snapshot_workspace(WORKSPACE_DIR)
        env = self._build_env(spec)
        process = await asyncio.create_subprocess_exec(
            *self._build_command(spec.shell_mode, command),
            cwd=str(workdir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if on_progress is not None and process.stdout is not None:
            # 流式路径：逐行读取 stdout，通过 on_progress 实时推送进度事件
            stdout_chunks: list[str] = []

            async def _drain_stdout() -> None:
                assert process.stdout is not None
                async for raw_line in process.stdout:
                    line = raw_line.decode("utf-8", errors="replace")
                    stdout_chunks.append(line)
                    await on_progress({"line": line.rstrip("\n\r"), "stream": "stdout"})

            try:
                stderr_task = asyncio.create_task(process.stderr.read() if process.stderr else asyncio.sleep(0))
                await asyncio.wait_for(_drain_stdout(), timeout=spec.timeout_s)
                stderr_raw = await stderr_task
                await process.wait()
            except asyncio.TimeoutError as exc:
                process.kill()
                await process.wait()
                raise TimeoutError(f"shell command timed out after {spec.timeout_s}s") from exc

            stdout_raw_str = "".join(stdout_chunks)
            stderr_bytes = stderr_raw if isinstance(stderr_raw, bytes) else b""
        else:
            # 原始路径（无回调）
            try:
                stdout_raw_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=spec.timeout_s
                )
            except asyncio.TimeoutError as exc:
                process.kill()
                await process.wait()
                raise TimeoutError(f"shell command timed out after {spec.timeout_s}s") from exc
            stdout_raw_str = stdout_raw_bytes.decode("utf-8", errors="replace")

        stdout = truncate_text(stdout_raw_str, spec.capture_output_limit)
        stderr = truncate_text(stderr_bytes.decode("utf-8", errors="replace"), spec.capture_output_limit)
        after = snapshot_workspace(WORKSPACE_DIR)
        changed_files = diff_workspace(before, after)
        ok = process.returncode == 0
        return ToolResult(
            call_id="",
            tool_name="shell",
            ok=ok,
            output=(stdout or "").strip() if ok else None,
            error=None if ok else (stderr or stdout or f"shell exited with code {process.returncode}").strip(),
            stdout=stdout,
            stderr=stderr,
            exit_code=process.returncode,
            changed_files=changed_files,
            sandbox_mode=spec.working_dir_mode,
            metadata={"command": command, "workdir": str(workdir.relative_to(WORKSPACE_DIR))},
        )

    def _resolve_workdir(self, spec: ShellToolSpec, workdir: str) -> Path:
        base = WORKSPACE_DIR.resolve()
        resolved = workspace_resolve(workdir)
        if spec.working_dir_mode == "workspace_only" and resolved != base and base not in resolved.parents:
            raise GuardrailViolation("shell workdir must stay inside workspace")
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _check_command_allowed(self, spec: ShellToolSpec, command: str) -> None:
        if spec.allowed_commands:
            prefix = command.strip().split()[0]
            if prefix not in spec.allowed_commands:
                raise GuardrailViolation(f"shell command '{prefix}' is not in the allowlist")
        for pattern in spec.blocked_patterns:
            if re.search(pattern, command, flags=re.IGNORECASE):
                raise GuardrailViolation(f"shell command blocked by pattern: {pattern}")

    def _build_env(self, spec: ShellToolSpec) -> dict[str, str]:
        if spec.env_policy == "empty":
            return {}
        if spec.env_policy == "safe_default":
            allowed = ["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"]
            return {key: value for key, value in os.environ.items() if key.upper() in allowed}
        if spec.env_policy == "inherit_filtered":
            blocked = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
            return {key: value for key, value in os.environ.items() if key not in blocked}
        raise ValueError(f"unsupported env policy: {spec.env_policy}")

    def _build_command(self, shell_mode: str, command: str) -> list[str]:
        if shell_mode == "powershell":
            return ["powershell", "-NoProfile", "-Command", command]
        if shell_mode == "bash":
            return ["bash", "-lc", command]
        if shell_mode == "sh":
            return ["sh", "-lc", command]
        raise ValueError(f"unsupported shell mode: {shell_mode}")


class ShellTool:
    """
    Shell工具（A5：arun 透传 on_progress 回调给 ShellExecutor）
    """
    def __init__(self, executor: ShellExecutor, spec_getter: Callable[[], ShellToolSpec]) -> None:
        self.executor = executor
        self.spec_getter = spec_getter

    async def arun(
        self,
        arguments: dict[str, Any],
        *,
        on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> ToolResult:
        return await self.executor.run(self.spec_getter(), arguments, on_progress=on_progress)


class ToolExecutor:
    """
    工具执行器
    """
    def __init__(
        self,
        registry: ToolRegistry,
        permission_controller: PermissionController,
        approval_controller: ApprovalController,
        guardrail_engine: GuardrailEngine,
        retry_policy: RetryPolicy,
        idempotency_store: IdempotencyStore,
        event_bus: EventBus,
    ) -> None:
        self.registry = registry
        self.permission_controller = permission_controller
        self.approval_controller = approval_controller
        self.guardrail_engine = guardrail_engine
        self.retry_policy = retry_policy
        self.idempotency_store = idempotency_store
        self.event_bus = event_bus

    async def execute(self, state: AgentState, call: ToolCall) -> ToolResult:
        start = time.time()
        try:
            spec = self.registry.get_spec(call.tool_name)
            tool = self.registry.get_tool(call.tool_name)
            self.guardrail_engine.validate_tool_args(call.tool_name, call.arguments)
            self.permission_controller.check_tool_permission(state, spec)
            await self.approval_controller.require_approval_if_needed(spec, call)
        except HumanInterventionRequired:
            raise
        except Exception as exc:
            result = ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=False,
                error=str(exc),
                latency_ms=int((time.time() - start) * 1000),
                metadata={"error_type": type(exc).__name__, "stage": "tool_preflight"},
                retry_count=0,
                approved_by=call.metadata.get("approved_by"),
            )
            await self.event_bus.publish(
                AgentEvent(
                    run_id=state.run_id,
                    event_type=EventType.TOOL_FAILED,
                    ts=time.time(),
                    step=state.step,
                    payload={
                        "tool_name": call.tool_name,
                        "ok": False,
                        "latency_ms": result.latency_ms,
                        "error": result.error,
                        "stage": "tool_preflight",
                    },
                )
            )
            return result

        if call.idempotency_key and spec.cache_policy != "none":
            cached = self.idempotency_store.get(call.idempotency_key)
            if cached is not None:
                return dataclasses.replace(cached, from_cache=True)

        sandbox_mode = spec.working_dir_mode if isinstance(spec, ShellToolSpec) else ("workspace_only" if spec.sandbox_required else None)
        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.TOOL_STARTED,
                ts=time.time(),
                step=state.step,
                payload={
                    "tool_name": call.tool_name,
                    "arguments": call.arguments,
                    "idempotency_key": call.idempotency_key,
                    "risk_level": spec.risk_level,
                    "side_effect": spec.side_effect,
                    "sandbox_mode": sandbox_mode,
                },
            )
        )

        async def _invoke_once() -> ToolResult:
            # A5: 若工具支持 on_progress 回调（ShellTool），注入进度事件发布
            if isinstance(tool, ShellTool):
                async def _on_progress(data: dict[str, Any]) -> None:
                    await self.event_bus.publish(
                        AgentEvent(
                            run_id=state.run_id,
                            event_type=EventType.TOOL_STARTED,  # 复用已有事件类型
                            event_kind="tool.progress",         # 新式 kind 直接覆盖
                            ts=time.time(),
                            step=state.step,
                            payload={
                                "tool_name": call.tool_name,
                                "call_id": call.call_id,
                                **data,
                            },
                        )
                    )
                raw = await asyncio.wait_for(
                    tool.arun(call.arguments, on_progress=_on_progress),
                    timeout=spec.timeout_s,
                )
            else:
                raw = await asyncio.wait_for(tool.arun(call.arguments), timeout=spec.timeout_s)
            result = self._normalize_result(call, raw)
            result.approved_by = call.metadata.get("approved_by")
            result.sandbox_mode = result.sandbox_mode or sandbox_mode
            self.guardrail_engine.validate_tool_result(result)
            return result

        def _retryable(exc: Exception) -> bool:
            return not isinstance(exc, (GuardrailViolation, PermissionDenied, HumanInterventionRequired, ValueError))

        try:
            result = await self.retry_policy.run(_invoke_once, retryable=_retryable, max_retries=spec.max_retries)
            result.latency_ms = int((time.time() - start) * 1000)
            result.retry_count = self.retry_policy.last_retry_count
        except Exception as exc:
            result = ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=False,
                error=str(exc),
                latency_ms=int((time.time() - start) * 1000),
                metadata={"error_type": type(exc).__name__},
                retry_count=self.retry_policy.last_retry_count,
                approved_by=call.metadata.get("approved_by"),
                sandbox_mode=sandbox_mode,
            )

        self._cache_result(spec, call, result)
        await self.event_bus.publish(
            AgentEvent(
                run_id=state.run_id,
                event_type=EventType.TOOL_FINISHED if result.ok else EventType.TOOL_FAILED,
                ts=time.time(),
                step=state.step,
                payload={
                    "tool_name": call.tool_name,
                    "ok": result.ok,
                    "risk_level": spec.risk_level,
                    "side_effect": spec.side_effect,
                    "from_cache": result.from_cache,
                    "retry_count": result.retry_count,
                    "latency_ms": result.latency_ms,
                    "error": result.error,
                },
            )
        )
        return result

    def _normalize_result(self, call: ToolCall, raw: Any) -> ToolResult:
        if isinstance(raw, ToolResult):
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=raw.ok,
                output=raw.output,
                error=raw.error,
                latency_ms=raw.latency_ms,
                metadata=dict(raw.metadata),
                stdout=raw.stdout,
                stderr=raw.stderr,
                exit_code=raw.exit_code,
                artifacts=list(raw.artifacts),
                changed_files=list(raw.changed_files),
                retry_count=raw.retry_count,
                from_cache=raw.from_cache,
                approved_by=raw.approved_by,
                sandbox_mode=raw.sandbox_mode,
                render_kind=raw.render_kind,
                render_payload=dict(raw.render_payload),
                truncated=raw.truncated,
                artifact_id=raw.artifact_id,
            )
        if isinstance(raw, dict):
            ok = bool(raw.get("ok", True))
            raw_error = raw.get("error")
            if not ok and not raw_error:
                raw_error = raw.get("stderr") or raw.get("stdout") or "tool execution failed"
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                ok=ok,
                output=json.dumps(raw, ensure_ascii=False),
                error=None if ok else str(raw_error),
                metadata=dict(raw.get("metadata", {})) if isinstance(raw.get("metadata"), dict) else {},
                stdout=raw.get("stdout") if isinstance(raw.get("stdout"), str) else None,
                stderr=raw.get("stderr") if isinstance(raw.get("stderr"), str) else None,
                exit_code=raw.get("exit_code") if isinstance(raw.get("exit_code"), int) else None,
                artifacts=list(raw.get("artifacts", [])) if isinstance(raw.get("artifacts"), list) else [],
                changed_files=list(raw.get("changed_files", [])) if isinstance(raw.get("changed_files"), list) else [],
                sandbox_mode=raw.get("sandbox_mode") if isinstance(raw.get("sandbox_mode"), str) else None,
            )
        return ToolResult(call_id=call.call_id, tool_name=call.tool_name, ok=True, output=str(raw))

    def _cache_result(self, spec: Any, call: ToolCall, result: ToolResult) -> None:
        if not call.idempotency_key or spec.cache_policy == "none":
            return
        if spec.cache_policy == "success_only" and not result.ok:
            return
        self.idempotency_store.set(call.idempotency_key, result)
