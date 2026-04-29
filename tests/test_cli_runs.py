from __future__ import annotations

import asyncio
from typing import Any

from packages.cli.commands import runs


async def _iter_lines(lines: list[str]):
    for line in lines:
        yield line


def test_build_run_events_url_accepts_root_api_url() -> None:
    assert runs._build_run_events_url("http://127.0.0.1:8000", "run-1") == "http://127.0.0.1:8000/api/events/runs/run-1"
    assert runs._build_run_events_url("http://127.0.0.1:8000/api", "run-1") == "http://127.0.0.1:8000/api/events/runs/run-1"


def test_parse_sse_events_yields_json_payloads() -> None:
    async def _collect() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for event in runs._parse_sse_events(
            _iter_lines(
                [
                    "id: 1",
                    "event: model.token",
                    'data: {"seq":1,"kind":"model.token","payload":{"token":"Hi"}}',
                    "",
                    ": ping",
                    "event: run.completed",
                    'data: {"seq":2,"kind":"run.completed","final_output":"done"}',
                    "",
                ]
            )
        ):
            events.append(event)
        return events

    events = asyncio.run(_collect())

    assert events == [
        {"seq": 1, "kind": "model.token", "payload": {"token": "Hi"}},
        {"seq": 2, "kind": "run.completed", "final_output": "done"},
    ]


def test_watch_uses_remote_when_api_url_given(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_watch_remote(run_id: str, timeout: int, api_url: str) -> None:
        captured["remote"] = (run_id, timeout, api_url)

    monkeypatch.setattr(runs, "_watch_remote", _fake_watch_remote)

    asyncio.run(runs._watch("run-1", 15, api_url="http://127.0.0.1:8000"))

    assert captured["remote"] == ("run-1", 15, "http://127.0.0.1:8000")