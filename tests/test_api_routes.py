"""
tests/test_api_routes.py — FastAPI 路由导入 + 基础端点测试

使用 httpx.AsyncClient + ASGITransport（不需要真实 LLM）验证：
- /health 返回 200
- /docs  返回 200（OpenAPI UI）
- /api/tools 返回 200
- 404 路由返回正确错误格式
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient, ASGITransport

from packages.app_service.chat_service import ChatRunResult
from packages.app_service.run_service import RunManager
from packages.runtime.events import EventBus
from packages.runtime.models import AgentEvent, EventKind, EventType


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def api_client():
    from packages.api.fastapi_app import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.mark.anyio
async def test_health(api_client: AsyncClient) -> None:
    r = await api_client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


@pytest.mark.anyio
async def test_docs_available(api_client: AsyncClient) -> None:
    r = await api_client.get("/docs")
    assert r.status_code == 200


@pytest.mark.anyio
async def test_tools_endpoint(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/tools")
    assert r.status_code == 200
    data = r.json()
    assert "tools" in data
    assert "count" in data


@pytest.mark.anyio
async def test_unknown_route_404(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/nonexistent")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_run_not_found(api_client: AsyncClient) -> None:
    # RunManager 没有通过 lifespan 初始化时返回 503，这里只验证不是 200
    r = await api_client.get("/api/runs/no-such-run")
    assert r.status_code in (404, 503)


@pytest.mark.anyio
async def test_chat_run_not_found(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/chat/runs/no-such-run")
    assert r.status_code == 404


class _FakeChatService:
    def __init__(self) -> None:
        self._runtime = type("FakeRuntime", (), {"event_bus": EventBus()})()

    async def chat(self, user_id: str, session_id: str, message: str) -> ChatRunResult:
        runtime_run_id = "runtime-fake-run-id"
        await self._runtime.event_bus.publish(
            AgentEvent(
                run_id=runtime_run_id,
                event_type=EventType.RUN_STARTED,
                event_kind=EventKind.run_started.value,
                ts=1.0,
                step=0,
                payload={"task": message, "session_id": session_id},
            )
        )
        await self._runtime.event_bus.publish(
            AgentEvent(
                run_id=runtime_run_id,
                event_type=EventType.MODEL_OUTPUT,
                event_kind=EventKind.model_token.value,
                ts=2.0,
                step=0,
                payload={"token": "echo:"},
            )
        )
        return ChatRunResult(
            run_id=runtime_run_id,
            status="completed",
            final_output=f"echo:{message}",
            failure_reason=None,
            cost_summary={"total_tokens": 3},
        )


@pytest.mark.anyio
async def test_chat_stream_returns_sse_events(api_client: AsyncClient) -> None:
    from packages.api.deps import get_chat_service
    from packages.api.fastapi_app import app

    app.state.run_manager = RunManager()
    app.dependency_overrides[get_chat_service] = lambda: _FakeChatService()
    try:
        async with api_client.stream(
            "POST",
            "/api/chat/stream",
            json={"message": "hello", "user_id": "u1", "session_id": "s1"},
        ) as response:
            assert response.status_code == 200
            lines: list[str] = []
            async for line in response.aiter_lines():
                if line:
                    lines.append(line)
                if any("run.completed" in item for item in lines) and any("echo:hello" in item for item in lines):
                    break
        text = "\n".join(lines)
        assert "event: run.started" in text
        assert "event: model.token" in text
        assert "event: run.completed" in text
        assert 'echo:hello' in text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_cancel_chat_run_route(api_client: AsyncClient) -> None:
    from packages.api.fastapi_app import app

    manager = RunManager()
    app.state.run_manager = manager
    run_id = "run-cancel-1"
    manager._cancel_events[run_id] = asyncio.Event()

    response = await api_client.delete(f"/api/chat/runs/{run_id}")
    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "cancelled": True}
