from __future__ import annotations

import pytest

from packages.runtime.llm_client import NativeToolCallingLLMClient


class _FakeChunk:
    def __init__(
        self,
        *,
        content: str = "",
        tool_call_chunks: list[dict] | None = None,
        additional_kwargs: dict | None = None,
    ) -> None:
        self.content = content
        self.tool_call_chunks = tool_call_chunks or []
        self.additional_kwargs = additional_kwargs or {}

    def __add__(self, other: "_FakeChunk") -> "_FakeChunk":
        return _FakeChunk(
            content=f"{self.content}{other.content}",
            tool_call_chunks=[*self.tool_call_chunks, *other.tool_call_chunks],
            additional_kwargs={**self.additional_kwargs, **other.additional_kwargs},
        )


class _FakeBoundLLM:
    async def astream(self, messages):
        yield _FakeChunk(content="Hel")
        yield _FakeChunk(
            content="lo",
            tool_call_chunks=[{"index": 0, "name": "file_read", "args": '{"path":"README.md"}'}],
            additional_kwargs={"reasoning_content": "thinking"},
        )


class _FakeRawLLM:
    def bind_tools(self, tool_schemas):
        return _FakeBoundLLM()


@pytest.mark.asyncio
async def test_invoke_with_stream_emits_callbacks() -> None:
    client = NativeToolCallingLLMClient(llm=_FakeRawLLM(), model_name="fake-model")
    tokens: list[str] = []
    thinking: list[str] = []
    tool_deltas: list[dict] = []

    result = await client.invoke_with_stream(
        messages=[{"role": "user", "content": "hello"}],
        tool_schemas=[],
        on_token=tokens.append,
        on_tool_call_delta=tool_deltas.append,
        on_thinking=thinking.append,
    )

    assert result.content == "Hello"
    assert tokens == ["Hel", "lo"]
    assert thinking == ["thinking"]
    assert tool_deltas == [{"index": 0, "name": "file_read", "args": '{"path":"README.md"}'}]