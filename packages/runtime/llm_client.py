from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

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

    def extract_content_and_tool_calls(self, response: Any) -> tuple[str, list[dict[str, Any]]]:
        content = self._coerce_content(getattr(response, "content", ""))
        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            return content, [
                {"id": item.get("id") or str(uuid.uuid4()), "name": item.get("name"), "arguments": item.get("args", {}) or {}}
                for item in tool_calls
            ]
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
        return content, normalized

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
