from __future__ import annotations

import asyncio
from pathlib import Path

import packages.runtime.bootstrap as bootstrap_module
from packages.runtime.config import DEFAULT_LOADED_TOOL_NAMES, MODEL_NAME, WORKSPACE_DIR, load_runtime_config


class _FakeLoader:
    def __init__(self, *, active_model_name: str = "fake-active", llm: object | None = None) -> None:
        self.active_model_name = active_model_name
        self._llm = llm if llm is not None else object()

    def load(self) -> object:
        return self._llm


def test_load_runtime_config_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_runtime_config(tmp_path / "missing-agent-config.yaml")

    assert cfg.model_name == MODEL_NAME
    assert cfg.workspace_root == WORKSPACE_DIR.resolve()
    assert cfg.default_loaded_tool_names == DEFAULT_LOADED_TOOL_NAMES
    assert cfg.source_path is None


def test_load_runtime_config_reads_yaml_values(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.config.yaml"
    workspace = tmp_path / "workspace"
    skill_a = tmp_path / "skills-a"
    skill_b = tmp_path / "skills-b"
    config_path.write_text(
        """
model:
  url: http://example.test/v1
  name: config-model
  api_key: secret-key
  temperature: 0.25
runtime:
  workspace_root: ./workspace
  loaded_tools:
    - file_read
    - grep
  short_memory_turns: 5
budget:
  max_steps: 11
  max_tool_calls: 12
  max_state_tool_calls: 13
  max_read_tool_calls: 14
  max_network_tool_calls: 15
  max_high_risk_tool_calls: 16
  max_seconds: 17
skills:
  roots:
    - ./skills-a
    - ./skills-b
""".strip(),
        encoding="utf-8",
    )

    cfg = load_runtime_config(config_path)

    assert cfg.model_url == "http://example.test/v1"
    assert cfg.model_name == "config-model"
    assert cfg.api_key == "secret-key"
    assert cfg.temperature == 0.25
    assert cfg.workspace_root == workspace.resolve()
    assert cfg.default_loaded_tool_names == ["file_read", "grep"]
    assert cfg.short_memory_turns == 5
    assert cfg.max_steps == 11
    assert cfg.max_tool_calls == 12
    assert cfg.max_state_tool_calls == 13
    assert cfg.max_read_tool_calls == 14
    assert cfg.max_network_tool_calls == 15
    assert cfg.max_high_risk_tool_calls == 16
    assert cfg.max_seconds == 17
    assert cfg.skill_roots == [skill_a.resolve(), skill_b.resolve()]
    assert cfg.source_path == config_path.resolve()


def test_build_runtime_uses_config_file(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    config_path = tmp_path / "agent.config.yaml"
    config_path.write_text(
        """
model:
  url: http://config-model/v1
  name: qwen-config
  api_key: cfg-key
  temperature: 0.15
runtime:
  workspace_root: ./configured-workspace
  loaded_tools:
    - file_read
    - grep
    - find_symbol
  short_memory_turns: 4
budget:
  max_steps: 7
  max_tool_calls: 8
  max_state_tool_calls: 9
  max_read_tool_calls: 10
  max_network_tool_calls: 11
  max_high_risk_tool_calls: 12
  max_seconds: 13
skills:
  roots:
    - ./configured-skills
""".strip(),
        encoding="utf-8",
    )

    def _fake_create_model_loader(*, model_url: str, model_name: str, api_key: str, temperature: float):
        captured.update(
            model_url=model_url,
            model_name=model_name,
            api_key=api_key,
            temperature=temperature,
        )
        return _FakeLoader(active_model_name="configured-active")

    monkeypatch.setattr(bootstrap_module, "create_model_loader", _fake_create_model_loader)

    runtime = bootstrap_module.build_runtime(config_path=config_path)
    file_read = runtime.registry.get_tool("file_read")

    assert captured == {
        "model_url": "http://config-model/v1",
        "model_name": "qwen-config",
        "api_key": "cfg-key",
        "temperature": 0.15,
    }
    assert file_read.workspace_root == (tmp_path / "configured-workspace").resolve()
    assert runtime.guardrail_engine.skill_roots == [(tmp_path / "configured-skills").resolve()]
    assert runtime.message_builder.short_memory_turns == 4
    assert runtime.message_builder.default_loaded_tool_names == ["file_read", "grep", "find_symbol"]
    assert runtime.budget_controller.max_steps == 7
    assert runtime.budget_controller.max_tool_calls == 8
    assert runtime.budget_controller.max_state_tool_calls == 9
    assert runtime.budget_controller.max_read_tool_calls == 10
    assert runtime.budget_controller.max_network_tool_calls == 11
    assert runtime.budget_controller.max_high_risk_tool_calls == 12
    assert runtime.budget_controller.max_seconds == 13
    assert runtime.llm_client.model_name == "configured-active"


def test_build_runtime_uses_configured_loaded_tools_in_initial_state(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "agent.config.yaml"
    config_path.write_text(
        """
runtime:
  loaded_tools:
    - file_read
    - grep
    - find_symbol
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(bootstrap_module, "create_model_loader", lambda **_: _FakeLoader())

    runtime = bootstrap_module.build_runtime(config_path=config_path)

    async def _fake_continue(state, cancel_ev=None):
      return state

    monkeypatch.setattr(runtime, "_continue", _fake_continue)
    state = asyncio.run(runtime.chat(user_id="u1", session_id="s1", message="hello"))

    assert state.metadata["loaded_tools"] == ["file_read", "grep", "find_symbol"]
    prompt = runtime.message_builder.build_system_prompt(state)
    assert "file_read, grep, find_symbol" in prompt


def test_build_runtime_workspace_argument_overrides_config(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "agent.config.yaml"
    config_path.write_text(
        """
runtime:
  workspace_root: ./configured-workspace
""".strip(),
        encoding="utf-8",
    )
    override_workspace = tmp_path / "explicit-workspace"

    monkeypatch.setattr(
        bootstrap_module,
        "create_model_loader",
        lambda **_: _FakeLoader(),
    )

    runtime = bootstrap_module.build_runtime(config_path=config_path, workspace_root=override_workspace)

    assert runtime.registry.get_tool("file_read").workspace_root == override_workspace.resolve()
    assert runtime.guardrail_engine.workspace_root == override_workspace.resolve()


def test_build_llm_uses_config_file(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    config_path = tmp_path / "agent.config.yaml"
    config_path.write_text(
        """
model:
  url: http://llm-config/v1
  name: planner-model
  api_key: planner-key
  temperature: 0.75
""".strip(),
        encoding="utf-8",
    )
    sentinel = object()

    def _fake_create_model_loader(*, model_url: str, model_name: str, api_key: str, temperature: float):
        captured.update(
            model_url=model_url,
            model_name=model_name,
            api_key=api_key,
            temperature=temperature,
        )
        return _FakeLoader(active_model_name="planner-active", llm=sentinel)

    monkeypatch.setattr(bootstrap_module, "create_model_loader", _fake_create_model_loader)

    llm = bootstrap_module.build_llm(config_path=config_path)

    assert llm is sentinel
    assert captured == {
        "model_url": "http://llm-config/v1",
        "model_name": "planner-model",
        "api_key": "planner-key",
        "temperature": 0.75,
    }


def test_build_system_prompt_falls_back_when_loaded_tools_missing_or_none(
    monkeypatch, tmp_path: Path
) -> None:
    """回归: state.metadata 没有 loaded_tools 键、或显式存了 None 时，
    build_system_prompt 应该走 runtime 配置的默认列表，而不是崩溃。"""
    from packages.runtime.models import AgentState, Phase, RunStatus

    config_path = tmp_path / "agent.config.yaml"
    config_path.write_text(
        """
runtime:
  loaded_tools:
    - file_read
    - grep
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(bootstrap_module, "create_model_loader", lambda **_: _FakeLoader())
    runtime = bootstrap_module.build_runtime(config_path=config_path)

    def _make_state(metadata: dict) -> AgentState:
        return AgentState(
            run_id="r",
            user_id="u",
            task="t",
            session_id="s",
            status=RunStatus.RUNNING,
            phase=Phase.DECIDING,
            conversation=[],
            metadata=metadata,
        )

    # 场景 A: 键缺失（老 checkpoint resume 路径）
    prompt_missing = runtime.message_builder.build_system_prompt(_make_state({}))
    assert "file_read, grep" in prompt_missing

    # 场景 B: 显式 None（曾经能让 dict.get 默认值失效并 TypeError）
    prompt_none = runtime.message_builder.build_system_prompt(
        _make_state({"loaded_tools": None})
    )
    assert "file_read, grep" in prompt_none

    # 场景 C: 空 list 也走 fallback，不留个空字段在 prompt 里
    prompt_empty = runtime.message_builder.build_system_prompt(
        _make_state({"loaded_tools": []})
    )
    assert "file_read, grep" in prompt_empty