from __future__ import annotations

from packages.cli import app as cli_app
from packages.cli.options import GlobalOptions


def test_split_repl_command_keeps_argument_text() -> None:
    cmd, arg = cli_app._split_repl_command("/plan 重构 CLI UI 像 Claude Code")

    assert cmd == "plan"
    assert arg == "重构 CLI UI 像 Claude Code"


def test_handle_plan_slash_command_invokes_plan_create(monkeypatch) -> None:
    captured: dict[str, object] = {}
    original_asyncio_run = cli_app.asyncio.run

    async def _fake_create_plan(goal: str, context: str, provider: str, json_output: bool) -> None:
        captured["goal"] = goal
        captured["context"] = context
        captured["provider"] = provider
        captured["json_output"] = json_output

    def _fake_asyncio_run(awaitable):
        return original_asyncio_run(awaitable)

    from packages.cli.commands import plans

    monkeypatch.setattr(plans, "_create_plan", _fake_create_plan)
    monkeypatch.setattr(cli_app.asyncio, "run", _fake_asyncio_run)

    cli_app._handle_slash(
        "/plan 给 CLI 做工作台 UI",
        GlobalOptions(provider="mock", user_id="u1", session_id="s1", json_output=True),
    )

    assert captured == {
        "goal": "给 CLI 做工作台 UI",
        "context": "",
        "provider": "mock",
        "json_output": True,
    }


def test_snapshot_workbench_tolerates_query_errors(monkeypatch) -> None:
    import packages.app_service.query_service as query_service

    def _raise_from_env():
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(query_service.QueryService, "from_env", _raise_from_env)

    assert cli_app._snapshot_workbench() == {}


def test_parse_history_limit_defaults_and_bounds() -> None:
    assert cli_app._parse_history_limit("") == 30
    assert cli_app._parse_history_limit("abc") == 30
    assert cli_app._parse_history_limit("0") == 1
    assert cli_app._parse_history_limit("5") == 5


def test_show_history_loads_current_session(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeQueryService:
        async def get_session_messages(self, session_id: str):
            captured["session_id"] = session_id
            return [{"role": "user", "content": "hello", "created_at": 1.0}]

    def _fake_from_env():
        return _FakeQueryService()

    def _fake_render(session_id: str, messages: list[dict], *, limit: int | None = None) -> None:
        captured["render_session_id"] = session_id
        captured["messages"] = messages
        captured["limit"] = limit

    import packages.app_service.query_service as query_service
    import packages.cli.render as render

    monkeypatch.setattr(query_service.QueryService, "from_env", _fake_from_env)
    monkeypatch.setattr(render, "render_session_history", _fake_render)

    cli_app._show_history(GlobalOptions(session_id="s-history"), "7")

    assert captured["session_id"] == "s-history"
    assert captured["render_session_id"] == "s-history"
    assert captured["messages"] == [{"role": "user", "content": "hello", "created_at": 1.0}]
    assert captured["limit"] == 7