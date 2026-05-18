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