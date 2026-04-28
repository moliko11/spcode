from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable

from .cost import TokenUsage, extract_usage_from_response
from .models import safe_json_dumps


class NativeToolCallingLLMClient:
    def __init__(self, llm: Any, model_name: str) -> None:
        self.model_name = model_name
        self.raw_llm = llm
        self._bound_cache: dict[str, Any] = {}

    def _bind_tools(self, llm: Any, tool_schemas: list[dict[str, Any]]) -> Any:
        if hasattr(llm, "bind_tools"):
            return llm.bind_tools(tool_schemas)
        raise RuntimeError("current llm object does not support bind_tools")

    async def invoke(self, messages: list[Any], tool_schemas: list[dict[str, Any]]) -> Any:
        cache_key = safe_json_dumps(tool_schemas)
        bound_llm = self._bound_cache.get(cache_key)
        if bound_llm is None:
            bound_llm = self._bind_tools(self.raw_llm, tool_schemas)
            self._bound_cache[cache_key] = bound_llm
        if hasattr(bound_llm, "ainvoke"):
            return await bound_llm.ainvoke(messages)
        return await asyncio.to_thread(bound_llm.invoke, messages)

    async def invoke_with_stream(
        self,
        messages: list[Any],
        tool_schemas: list[dict[str, Any]],
        on_token: Callable[[str], Awaitable[None] | None] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        on_thinking: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> Any:
        """
        优先使用 astream 获取增量 token；若底层模型不支持则退化到普通 invoke。
        返回值与 invoke() 一致（完整响应对象）。
        """
        cache_key = safe_json_dumps(tool_schemas)
        bound_llm = self._bound_cache.get(cache_key)
        if bound_llm is None:
            bound_llm = self._bind_tools(self.raw_llm, tool_schemas)
            self._bound_cache[cache_key] = bound_llm

        if hasattr(bound_llm, "astream"):
            merged: Any | None = None
            try:
                async for chunk in bound_llm.astream(messages):
                    text = self._coerce_content(getattr(chunk, "content", ""))
                    if text and on_token is not None:
                        maybe = on_token(text)
                        if asyncio.iscoroutine(maybe):
                            await maybe
                    if on_tool_call_delta is not None:
                        for item in getattr(chunk, "tool_call_chunks", None) or []:
                            if not isinstance(item, dict):
                                continue
                            maybe = on_tool_call_delta(item)
                            if asyncio.iscoroutine(maybe):
                                await maybe
                    extra = getattr(chunk, "additional_kwargs", {}) or {}
                    reasoning = extra.get("reasoning_content") or extra.get("reasoning")
                    if reasoning and on_thinking is not None:
                        maybe = on_thinking(str(reasoning))
                        if asyncio.iscoroutine(maybe):
                            await maybe
                    merged = chunk if merged is None else (merged + chunk)
                if merged is not None:
                    return merged
            except Exception:
                # streaming 路径失败时退化到普通调用，避免中断主流程
                pass

        return await self.invoke(messages, tool_schemas)

    def extract_content_and_tool_calls(self, response: Any) -> tuple[str, list[dict[str, Any]], TokenUsage]:
        content = self._coerce_content(getattr(response, "content", ""))
        usage = extract_usage_from_response(response)
        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            return content, [
                {"id": item.get("id") or str(uuid.uuid4()), "name": item.get("name"), "arguments": item.get("args", {}) or {}}
                for item in tool_calls
            ], usage
        additional_kwargs = getattr(response, "additional_kwargs", {}) or {}
        raw_tool_calls = additional_kwargs.get("tool_calls", []) or []
        normalized = []
        for item in raw_tool_calls:
            fn = item.get("function", {}) or {}
            args = fn.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            normalized.append({"id": item.get("id") or str(uuid.uuid4()), "name": fn.get("name"), "arguments": args if isinstance(args, dict) else {}})
        return content, normalized, usage

    @staticmethod
    def _coerce_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts).strip()
        return str(content)
