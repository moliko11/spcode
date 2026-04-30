from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .config import (
    MODEL_INPUT_AUDIT_ENABLED,
    MODEL_INPUT_AUDIT_INCLUDE_CONTENT,
    MODEL_INPUT_AUDIT_LOG_PATH,
    logger,
)


_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


class ModelInputAuditor:
    """Record what is sent to the model before each model invocation."""

    def __init__(
        self,
        *,
        path: Path = MODEL_INPUT_AUDIT_LOG_PATH,
        enabled: bool = MODEL_INPUT_AUDIT_ENABLED,
        include_content: bool = MODEL_INPUT_AUDIT_INCLUDE_CONTENT,
    ) -> None:
        self.path = path
        self.enabled = enabled
        self.include_content = include_content

    async def audit(
        self,
        *,
        run_id: str,
        step: int,
        model_name: str,
        messages: list[Any],
        tool_schemas: list[dict[str, Any]],
    ) -> dict[str, Any]:
        tool_call_map = self._tool_call_map(messages)
        message_records = [self._message_record(idx, msg, tool_call_map) for idx, msg in enumerate(messages)]
        schema_text = json.dumps(tool_schemas, ensure_ascii=False, sort_keys=True)
        schema_stats = self._text_stats(schema_text)
        totals = {
            "messages": len(messages),
            "tool_schemas": len(tool_schemas),
            "chars": sum(item["stats"]["chars"] for item in message_records) + schema_stats["chars"],
            "ascii_words": sum(item["stats"]["ascii_words"] for item in message_records) + schema_stats["ascii_words"],
            "cjk_chars": sum(item["stats"]["cjk_chars"] for item in message_records) + schema_stats["cjk_chars"],
            "estimated_tokens": sum(item["stats"]["estimated_tokens"] for item in message_records)
            + schema_stats["estimated_tokens"],
        }
        by_role: dict[str, dict[str, int]] = {}
        for item in message_records:
            role = item["role"]
            bucket = by_role.setdefault(role, {"messages": 0, "chars": 0, "estimated_tokens": 0})
            bucket["messages"] += 1
            bucket["chars"] += item["stats"]["chars"]
            bucket["estimated_tokens"] += item["stats"]["estimated_tokens"]

        top_messages = sorted(
            [
                {
                    "index": item["index"],
                    "role": item["role"],
                    "chars": item["stats"]["chars"],
                    "estimated_tokens": item["stats"]["estimated_tokens"],
                    "tool_call_id": item.get("tool_call_id"),
                    "source_tool": item.get("source_tool"),
                }
                for item in message_records
            ],
            key=lambda item: item["chars"],
            reverse=True,
        )[:8]

        record: dict[str, Any] = {
            "ts": time.time(),
            "run_id": run_id,
            "step": step,
            "model_name": model_name,
            "totals": totals,
            "by_role": by_role,
            "schema_stats": {
                "count": len(tool_schemas),
                **schema_stats,
            },
            "top_messages": top_messages,
            "messages": message_records if self.include_content else self._strip_content(message_records),
            "tool_schemas": tool_schemas if self.include_content else [],
        }
        if self.enabled:
            logger.info(
                "model_input_audit run_id=%s step=%s model=%s messages=%d schemas=%d chars=%d ascii_words=%d cjk_chars=%d est_tokens=%d top=%s",
                run_id,
                step,
                model_name,
                totals["messages"],
                totals["tool_schemas"],
                totals["chars"],
                totals["ascii_words"],
                totals["cjk_chars"],
                totals["estimated_tokens"],
                top_messages[:3],
            )
            line = json.dumps(record, ensure_ascii=False) + "\n"
            await asyncio.to_thread(self._write_line, line)
        return record

    def _write_line(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def _message_record(self, index: int, message: Any, tool_call_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
        role = self._role(message)
        content = self._content(message)
        serialized = self._serialize_message(message, role, content)
        serialized_text = json.dumps(serialized, ensure_ascii=False, sort_keys=True)
        record = {
            "index": index,
            "role": role,
            "stats": self._text_stats(serialized_text),
            "content_stats": self._text_stats(content),
            "content": content,
        }
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id:
            record["tool_call_id"] = tool_call_id
            source = tool_call_map.get(str(tool_call_id))
            if source:
                record["source_tool"] = source.get("name")
                record["source_args"] = source.get("args")
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            record["tool_calls"] = tool_calls
        return record

    def _tool_call_map(self, messages: list[Any]) -> dict[str, dict[str, Any]]:
        tool_calls: dict[str, dict[str, Any]] = {}
        for message in messages:
            for item in getattr(message, "tool_calls", None) or []:
                if not isinstance(item, dict):
                    continue
                call_id = item.get("id")
                if not call_id:
                    continue
                tool_calls[str(call_id)] = {
                    "name": item.get("name"),
                    "args": item.get("args") or item.get("arguments") or {},
                }
        return tool_calls

    def _serialize_message(self, message: Any, role: str, content: str) -> dict[str, Any]:
        data: dict[str, Any] = {"role": role, "content": content}
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id:
            data["tool_call_id"] = tool_call_id
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            data["tool_calls"] = tool_calls
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if additional_kwargs:
            data["additional_kwargs"] = additional_kwargs
        return data

    def _role(self, message: Any) -> str:
        if isinstance(message, SystemMessage):
            return "system"
        if isinstance(message, HumanMessage):
            return "human"
        if isinstance(message, AIMessage):
            return "ai"
        if isinstance(message, ToolMessage):
            return "tool"
        return getattr(message, "type", type(message).__name__).lower()

    def _content(self, message: Any) -> str:
        content = getattr(message, "content", "")
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False, sort_keys=True)

    def _text_stats(self, text: str) -> dict[str, int]:
        chars = len(text)
        cjk_chars = len(_CJK_RE.findall(text))
        ascii_words = len(_ASCII_WORD_RE.findall(text))
        # Rough token estimate for quick trend analysis. API usage remains the
        # authoritative token count after the model returns.
        estimated_tokens = max(1, (chars + 3) // 4) if chars else 0
        return {
            "chars": chars,
            "ascii_words": ascii_words,
            "cjk_chars": cjk_chars,
            "estimated_tokens": estimated_tokens,
        }

    def _strip_content(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stripped = []
        for record in records:
            item = {key: value for key, value in record.items() if key != "content"}
            stripped.append(item)
        return stripped
