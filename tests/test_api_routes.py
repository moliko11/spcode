"""
tests/test_api_routes.py — FastAPI 路由导入 + 基础端点测试

使用 httpx.AsyncClient + ASGITransport（不需要真实 LLM）验证：
- /health 返回 200
- /docs  返回 200（OpenAPI UI）
- /api/tools 返回 200
- 404 路由返回正确错误格式
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport


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
