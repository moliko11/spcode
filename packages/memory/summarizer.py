"""
packages/memory/summarizer.py
──────────────────────────────
TranscriptSummarizer: LLM-based summarizer for Level 4 autocompact.

Wraps any LangChain-compatible LLM (or any object with an async ``ainvoke``
method) to produce a compact text summary of a slice of conversation history.
"""
from __future__ import annotations

from typing import Any

SUMMARIZE_PROMPT = """\
You are a concise assistant summarizing an AI coding agent's conversation history.

Summarize the following conversation in 150-300 words, capturing:
- The original task or goal
- Key files inspected or modified
- Tools called and their important findings
- Decisions made or problems encountered
- Current progress state

Conversation:
{transcript}

Summary:"""


def _format_messages(messages: list[dict[str, Any]], task: str = "") -> str:
    """Convert runtime message dicts to a readable transcript string."""
    lines: list[str] = []
    if task:
        lines.append(f"Task: {task}\n")
    for msg in messages:
        msg_type = msg.get("type", "unknown")
        content = str(msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls") or []
        if msg_type == "system":
            continue
        elif msg_type == "human":
            if content:
                lines.append(f"[User]: {content[:300]}")
        elif msg_type == "ai":
            if tool_calls:
                names = [tc.get("name", "?") for tc in tool_calls]
                lines.append(f"[Assistant calls tools: {', '.join(names)}]")
            elif content:
                lines.append(f"[Assistant]: {content[:300]}")
        elif msg_type == "tool":
            tc_id = str(msg.get("tool_call_id", ""))[:12]
            lines.append(f"[Tool result id={tc_id}]: {content[:150]}")
    return "\n".join(lines)


class TranscriptSummarizer:
    """Wraps an LLM client to summarize a slice of conversation history.

    Parameters
    ----------
    llm:
        Any object with an ``ainvoke(prompt: str)`` coroutine method.
        LangChain ``BaseChatModel`` instances satisfy this contract.
    """

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def summarize(self, messages: list[dict[str, Any]], task: str = "") -> str:
        """Return a compact text summary of the provided message dicts.

        Never raises — on LLM failure returns a placeholder string so that
        the compaction pipeline can continue without crashing.
        """
        transcript = _format_messages(messages, task=task)
        if not transcript.strip():
            return "(no content to summarize)"
        prompt = SUMMARIZE_PROMPT.format(transcript=transcript)
        try:
            response = await self._llm.ainvoke(prompt)
        except Exception as exc:  # noqa: BLE001
            return f"(summarization failed: {exc!s})"
        if hasattr(response, "content"):
            return str(response.content).strip()
        return str(response).strip()
