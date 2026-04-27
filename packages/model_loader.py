from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterator, Literal

import yaml
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, SecretStr

logger = logging.getLogger("model_loader")

DEFAULT_LOCAL_BASE_URL = "http://10.8.160.47:9998/v1"
DEFAULT_LOCAL_MODEL = "qwen3"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_MODEL = "qwen3.5-plus"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_YAML_PATH = Path(__file__).resolve().parent.parent / "models.yaml"


class DeepSeekChatOpenAI(ChatOpenAI):
    def _get_request_payload(self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload_messages = payload.get("messages")
        if not isinstance(payload_messages, list):
            return payload

        for source, target in zip(messages, payload_messages):
            if not isinstance(source, AIMessage):
                continue
            if target.get("role") != "assistant" or "tool_calls" not in target:
                continue
            reasoning_content = self._extract_reasoning_content(source)
            target["reasoning_content"] = reasoning_content
        return payload

    @staticmethod
    def _extract_reasoning_content(message: AIMessage) -> str:
        additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
        if isinstance(additional_kwargs, dict):
            value = additional_kwargs.get("reasoning_content")
            if value is not None:
                return str(value)
        response_metadata = getattr(message, "response_metadata", {}) or {}
        if isinstance(response_metadata, dict):
            value = response_metadata.get("reasoning_content")
            if value is not None:
                return str(value)
        return ""


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai_compatible"] = Field(default="openai_compatible")
    model_url: str
    model_name: str
    api_key: SecretStr = Field(default=SecretStr("EMPTY"))
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2000, ge=1)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    frequency_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)
    presence_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)
    timeout: int = Field(default=60, ge=1)
    max_retries: int = Field(default=3, ge=0)
    streaming: bool = Field(default=False)
    verbose: bool = Field(default=False)
    model_kwargs: Dict[str, Any] = Field(default_factory=dict)
    label: str = Field(default="default")
    priority: int = Field(default=99)


def load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _resolve_env_vars(value: str) -> str:
    def _replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        resolved = os.getenv(var_name, "")
        if not resolved:
            logger.warning("env var '%s' not set, model using empty api_key", var_name)
        return resolved

    return re.sub(r"\$\{(\w+)\}", _replacer, value)


def load_yaml_configs(path: str | Path = DEFAULT_YAML_PATH) -> list[ModelConfig]:
    yaml_path = Path(path)
    if not yaml_path.exists():
        logger.info("yaml config not found: %s, using defaults", yaml_path)
        return []

    load_env_file()

    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not raw or "models" not in raw:
        logger.warning("yaml config has no 'models' key: %s", yaml_path)
        return []

    configs: list[ModelConfig] = []
    for item in raw["models"]:
        if item.get("api_key"):
            item["api_key"] = _resolve_env_vars(str(item["api_key"]))
        priority = item.pop("priority", 99)
        item["priority"] = priority
        try:
            config = ModelConfig(**item)
            configs.append(config)
        except Exception as exc:
            logger.warning("skipping invalid model config '%s': %s", item.get("label", "?"), exc)

    configs.sort(key=lambda c: c.priority)
    return configs


class FallbackChatModel:
    def __init__(self, configs: list[ModelConfig], bound_tools: list[dict[str, Any]] | None = None) -> None:
        self.configs = configs
        self.bound_tools = bound_tools or []
        self._models: dict[str, Any] = {}

    def bind_tools(self, tool_schemas: list[dict[str, Any]]) -> "FallbackChatModel":
        return FallbackChatModel(self.configs, bound_tools=tool_schemas)

    async def ainvoke(self, messages: Any) -> Any:
        last_error: Exception | None = None
        for config in self.configs:
            try:
                model = self._get_bound_model(config)
                return await model.ainvoke(self._prepare_messages(config, messages))
            except Exception as exc:
                last_error = exc
                self._log_fallback(config, exc)
        assert last_error is not None
        raise last_error

    def invoke(self, messages: Any) -> Any:
        last_error: Exception | None = None
        for config in self.configs:
            try:
                model = self._get_bound_model(config)
                return model.invoke(self._prepare_messages(config, messages))
            except Exception as exc:
                last_error = exc
                self._log_fallback(config, exc)
        assert last_error is not None
        raise last_error

    async def astream(self, messages: Any):
        last_error: Exception | None = None
        for config in self.configs:
            try:
                model = self._get_bound_model(config)
                async for chunk in model.astream(self._prepare_messages(config, messages)):
                    yield chunk
                return
            except Exception as exc:
                last_error = exc
                self._log_fallback(config, exc)
        assert last_error is not None
        raise last_error

    def stream(self, messages: Any) -> Iterator[Any]:
        last_error: Exception | None = None
        for config in self.configs:
            try:
                model = self._get_bound_model(config)
                yield from model.stream(self._prepare_messages(config, messages))
                return
            except Exception as exc:
                last_error = exc
                self._log_fallback(config, exc)
        assert last_error is not None
        raise last_error

    def _get_bound_model(self, config: ModelConfig) -> Any:
        cache_key = f"{config.label}|{bool(self.bound_tools)}"
        if cache_key not in self._models:
            chat_model_cls = DeepSeekChatOpenAI if self._is_deepseek(config) else ChatOpenAI
            model = chat_model_cls(
                model=config.model_name,
                base_url=config.model_url,
                api_key=config.api_key.get_secret_value(),
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                top_p=config.top_p,
                frequency_penalty=config.frequency_penalty,
                presence_penalty=config.presence_penalty,
                timeout=config.timeout,
                max_retries=config.max_retries,
                streaming=config.streaming,
                verbose=config.verbose,
                model_kwargs=config.model_kwargs,
            )
            if self.bound_tools:
                model = model.bind_tools(self.bound_tools)
            self._models[cache_key] = model
        return self._models[cache_key]

    def _log_fallback(self, config: ModelConfig, exc: Exception) -> None:
        logger.warning("backend '%s' failed, trying next: %s: %s", config.label, type(exc).__name__, exc)

    def _prepare_messages(self, config: ModelConfig, messages: Any) -> Any:
        if not isinstance(messages, list):
            return messages
        is_deepseek = self._is_deepseek(config)
        return [self._prepare_message_for_backend(message, is_deepseek) for message in messages]

    def _prepare_message_for_backend(self, message: Any, is_deepseek: bool) -> Any:
        if not isinstance(message, AIMessage):
            return message
        additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
        if is_deepseek:
            if getattr(message, "tool_calls", None) and "reasoning_content" not in additional_kwargs:
                additional_kwargs["reasoning_content"] = ""
        else:
            additional_kwargs.pop("reasoning_content", None)
            additional_kwargs.pop("reasoning", None)
        if additional_kwargs == (getattr(message, "additional_kwargs", {}) or {}):
            return message
        return message.model_copy(update={"additional_kwargs": additional_kwargs})

    def _is_deepseek(self, config: ModelConfig) -> bool:
        text = f"{config.label} {config.model_name} {config.model_url}".lower()
        return "deepseek" in text


class ModelLoader:
    def __init__(self, config: ModelConfig, fallback_configs: list[ModelConfig] | None = None):
        self.config = config
        self.fallback_configs = fallback_configs or []
        self._model: Any | None = None

    def load(self) -> Any:
        if self._model is None:
            self._model = self._create_model()
        return self._model

    def reload(self) -> Any:
        self._model = None
        return self.load()

    def _create_model(self) -> Any:
        configs = [self.config, *self.fallback_configs]
        return FallbackChatModel(configs)

    async def astream(self, messages: Any):
        model = self.load()
        async for chunk in model.astream(messages):
            yield chunk

    async def ainvoke(self, messages: Any) -> Any:
        model = self.load()
        return await model.ainvoke(messages)

    def invoke(self, messages: Any) -> Any:
        model = self.load()
        return model.invoke(messages)

    def stream(self, messages: Any) -> Iterator[Any]:
        model = self.load()
        yield from model.stream(messages)

    def update_config(self, **kwargs: Any) -> None:
        updated_data = self.config.model_dump()
        updated_data.update(kwargs)
        self.config = ModelConfig(**updated_data)
        self._model = None

    @property
    def active_model_name(self) -> str:
        return self.config.model_name

    @property
    def active_model_url(self) -> str:
        return self.config.model_url

    def get_model_info(self) -> Dict[str, Any]:
        backends = [self.config, *self.fallback_configs]
        return {
            "provider": self.config.provider,
            "active_model_name": self.config.model_name,
            "active_model_url": self.config.model_url,
            "backends": [
                {
                    "label": item.label,
                    "model_name": item.model_name,
                    "model_url": item.model_url,
                    "priority": item.priority,
                }
                for item in backends
            ],
        }


def build_default_model_chain(
    local_model_url: str = DEFAULT_LOCAL_BASE_URL,
    local_model_name: str = DEFAULT_LOCAL_MODEL,
    local_api_key: str = "EMPTY",
    **kwargs: Any,
) -> list[ModelConfig]:
    load_env_file()

    configs: list[ModelConfig] = []
    qwen_api_key = os.getenv("QWEN_API_KEY")
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")

    if qwen_api_key:
        configs.append(
            ModelConfig(
                label="qwen_primary",
                model_url=os.getenv("QWEN_BASE_URL", DEFAULT_QWEN_BASE_URL),
                model_name=os.getenv("QWEN_MODEL", DEFAULT_QWEN_MODEL),
                api_key=SecretStr(qwen_api_key),
                **kwargs,
            )
        )

    if deepseek_api_key:
        configs.append(
            ModelConfig(
                label="deepseek_fallback",
                model_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
                model_name=os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
                api_key=SecretStr(deepseek_api_key),
                **kwargs,
            )
        )

    configs.append(
        ModelConfig(
            label="local_fallback",
            model_url=os.getenv("LOCAL_MODEL_URL", local_model_url),
            model_name=os.getenv("LOCAL_MODEL_NAME", local_model_name),
            api_key=SecretStr(os.getenv("LOCAL_MODEL_API_KEY", local_api_key)),
            **kwargs,
        )
    )
    return configs


def create_model_loader(
    model_url: str | None = None,
    model_name: str | None = None,
    api_key: str = "EMPTY",
    yaml_path: str | Path | None = None,
    **kwargs: Any,
) -> ModelLoader:
    yaml_configs = load_yaml_configs(yaml_path or DEFAULT_YAML_PATH)

    if yaml_configs:
        logger.info(
            "loaded %d model(s) from yaml, priority order: %s",
            len(yaml_configs),
            [c.label for c in yaml_configs],
        )
        return ModelLoader(config=yaml_configs[0], fallback_configs=yaml_configs[1:])

    logger.info("no yaml configs loaded, falling back to default chain")
    effective_url = model_url or DEFAULT_LOCAL_BASE_URL
    effective_name = model_name or DEFAULT_LOCAL_MODEL
    configs = build_default_model_chain(
        local_model_url=effective_url,
        local_model_name=effective_name,
        local_api_key=api_key,
        **kwargs,
    )
    return ModelLoader(config=configs[0], fallback_configs=configs[1:])
