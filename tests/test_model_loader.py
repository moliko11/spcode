from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import packages.model_loader as m


class FakeBackend:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.bound_tools = None

    def bind_tools(self, tool_schemas):
        bound = FakeBackend(self.responses, self.error)
        bound.bound_tools = tool_schemas
        return bound

    async def ainvoke(self, messages):
        if self.error:
            raise self.error
        return self.responses[0] if self.responses else {"ok": True}

    def invoke(self, messages):
        if self.error:
            raise self.error
        return self.responses[0] if self.responses else {"ok": True}

    async def astream(self, messages):
        if self.error:
            raise self.error
        for item in self.responses:
            yield item

    def stream(self, messages):
        if self.error:
            raise self.error
        yield from self.responses


def test_load_env_file_sets_missing_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("QWEN_API_KEY=qwen-key\nDEEPSEEK_API_KEY=deepseek-key\n", encoding="utf-8")
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    m.load_env_file(env_file)

    assert m.os.getenv("QWEN_API_KEY") == "qwen-key"
    assert m.os.getenv("DEEPSEEK_API_KEY") == "deepseek-key"


def test_build_default_model_chain_prefers_qwen_then_deepseek_then_local(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "qwen-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("QWEN_MODEL", "qwen-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test")
    monkeypatch.setenv("LOCAL_MODEL_NAME", "local-test")

    configs = m.build_default_model_chain(local_model_url="http://local/v1", local_model_name="local-default")

    assert [item.label for item in configs] == [
        "qwen_primary",
        "deepseek_fallback",
        "local_fallback",
    ]
    assert configs[0].model_name == "qwen-test"
    assert configs[1].model_name == "deepseek-test"
    assert configs[2].model_name == "local-test"


def test_fallback_chat_model_uses_next_backend_when_previous_fails(monkeypatch):
    created = []

    def fake_chat_openai(**kwargs):
        created.append(kwargs["model"])
        if kwargs["model"] == "qwen-first":
            return FakeBackend(error=RuntimeError("qwen down"))
        return FakeBackend(responses=[{"provider": kwargs["model"]}])

    monkeypatch.setattr(m, "ChatOpenAI", fake_chat_openai)
    monkeypatch.setattr(m, "DeepSeekChatOpenAI", fake_chat_openai)

    model = m.FallbackChatModel(
        [
            m.ModelConfig(model_url="http://qwen/v1", model_name="qwen-first", api_key=m.SecretStr("k1"), label="qwen"),
            m.ModelConfig(model_url="http://deepseek/v1", model_name="deepseek-second", api_key=m.SecretStr("k2"), label="deepseek"),
        ]
    )

    result = model.invoke([{"role": "user", "content": "hi"}])

    assert result == {"provider": "deepseek-second"}
    assert created == ["qwen-first", "deepseek-second"]


def test_deepseek_messages_get_reasoning_content(monkeypatch):
    captured = []

    class CaptureBackend(FakeBackend):
        def invoke(self, messages):
            captured.append(messages)
            return {"ok": True}

    monkeypatch.setattr(m, "ChatOpenAI", lambda **kwargs: CaptureBackend())
    monkeypatch.setattr(m, "DeepSeekChatOpenAI", lambda **kwargs: CaptureBackend())
    model = m.FallbackChatModel(
        [
            m.ModelConfig(
                model_url="https://api.deepseek.com/v1",
                model_name="deepseek-v4-pro",
                api_key=m.SecretStr("k"),
                label="deepseek_cloud",
            )
        ]
    )
    message = AIMessage(content="", tool_calls=[{"id": "call_1", "name": "tool", "args": {}}])

    model.invoke([message])

    assert captured[0][0].additional_kwargs["reasoning_content"] == ""


def test_non_deepseek_messages_strip_reasoning_content(monkeypatch):
    captured = []

    class CaptureBackend(FakeBackend):
        def invoke(self, messages):
            captured.append(messages)
            return {"ok": True}

    monkeypatch.setattr(m, "ChatOpenAI", lambda **kwargs: CaptureBackend())
    model = m.FallbackChatModel(
        [
            m.ModelConfig(
                model_url="http://local/v1",
                model_name="qwen3",
                api_key=m.SecretStr("k"),
                label="qwen_local",
            )
        ]
    )
    message = AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": "tool", "args": {}}],
        additional_kwargs={"reasoning_content": "trace"},
    )

    model.invoke([message])

    assert "reasoning_content" not in captured[0][0].additional_kwargs


def test_deepseek_payload_includes_reasoning_content() -> None:
    model = m.DeepSeekChatOpenAI(
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        api_key="k",
    )
    messages = [
        HumanMessage(content="hi"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_1", "name": "web_search", "args": {"query": "x"}}],
            additional_kwargs={"reasoning_content": "trace"},
        ),
    ]

    payload = model._get_request_payload(messages)

    assert payload["messages"][1]["reasoning_content"] == "trace"


def test_deepseek_payload_adds_empty_reasoning_content_when_missing() -> None:
    model = m.DeepSeekChatOpenAI(
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        api_key="k",
    )
    messages = [
        HumanMessage(content="hi"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_1", "name": "web_search", "args": {"query": "x"}}],
        ),
    ]

    payload = model._get_request_payload(messages)

    assert payload["messages"][1]["reasoning_content"] == ""


def test_create_model_loader_uses_chain_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("QWEN_API_KEY", "qwen-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("QWEN_MODEL", "qwen3.5-max")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v3.1")

    fake_yaml = tmp_path / "nonexistent_models.yaml"
    loader = m.create_model_loader(
        model_url="http://local/v1",
        model_name="qwen3",
        api_key="EMPTY",
        temperature=0.5,
        yaml_path=fake_yaml,
    )

    info = loader.get_model_info()
    assert [item["label"] for item in info["backends"]] == [
        "qwen_primary",
        "deepseek_fallback",
        "local_fallback",
    ]
