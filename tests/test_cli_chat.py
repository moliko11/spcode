from __future__ import annotations

from packages.cli.commands import chat
from packages.cli.options import GlobalOptions


class _FakeRoot:
    def __init__(self, obj: GlobalOptions | None) -> None:
        self.obj = obj


class _FakeContext:
    def __init__(self, obj: GlobalOptions | None) -> None:
        self._root = _FakeRoot(obj)

    def find_root(self) -> _FakeRoot:
        return self._root


def test_chat_cmd_enters_repl_when_message_missing(monkeypatch) -> None:
    captured: dict[str, GlobalOptions] = {}

    def _fake_repl(opts: GlobalOptions) -> None:
        captured["opts"] = opts

    monkeypatch.setattr(chat, "_run_chat_repl", _fake_repl)

    chat.chat_cmd(
        ctx=_FakeContext(GlobalOptions(provider="mock", user_id="u1", session_id="s1")),
        message=None,
        provider="openai_compatible",
        user_id="demo-user",
        session_id="demo-session",
        json_output=False,
        print_mode=False,
    )

    assert captured["opts"].provider == "mock"
    assert captured["opts"].user_id == "u1"
    assert captured["opts"].session_id == "s1"


def test_chat_cmd_local_options_override_root(monkeypatch) -> None:
    captured: dict[str, GlobalOptions | str] = {}
    original_asyncio_run = chat.asyncio.run

    async def _fake_do_chat(opts: GlobalOptions, message: str) -> None:
        captured["opts"] = opts
        captured["message"] = message

    def _fake_asyncio_run(awaitable):
        return original_asyncio_run(awaitable)

    monkeypatch.setattr(chat, "_do_chat", _fake_do_chat)
    monkeypatch.setattr(chat.asyncio, "run", _fake_asyncio_run)

    chat.chat_cmd(
        ctx=_FakeContext(GlobalOptions(provider="mock", user_id="u1", session_id="s1")),
        message="hello",
        provider="openai_compatible",
        user_id="override-user",
        session_id="override-session",
        json_output=True,
        print_mode=False,
    )

    opts = captured["opts"]
    assert isinstance(opts, GlobalOptions)
    assert opts.provider == "mock"
    assert opts.user_id == "override-user"
    assert opts.session_id == "override-session"
    assert opts.json_output is True
    assert captured["message"] == "hello"