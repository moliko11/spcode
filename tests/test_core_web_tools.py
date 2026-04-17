from __future__ import annotations

import asyncio

import httpx

from packages.core_io.web_tools import WebSearchTool


class FakeResponse:
    def __init__(self, *, json_data=None, text="", status_code=200):
        self._json_data = json_data
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self):
        return self._json_data


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None):
        self.calls.append(("POST", url, json))
        if "tavily" in url:
            return FakeResponse(
                json_data={
                    "results": [
                        {"title": "T1", "url": "https://example.com/1", "content": "snippet one"},
                        {"title": "T2", "url": "https://example.com/2", "content": "snippet two"},
                    ]
                }
            )
        raise AssertionError("unexpected POST")

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params))
        if url == "https://example.com/1":
            return FakeResponse(text="<html><body>page one</body></html>")
        if url == "https://example.com/2":
            return FakeResponse(text="<html><body>page two</body></html>")
        if "serpapi.com" in url:
            return FakeResponse(
                json_data={"organic_results": [{"title": "S1", "link": "https://example.com/s", "snippet": "serp"}]}
            )
        if "duckduckgo.com" in url:
            return FakeResponse(text='<a class="result__a" href="https://example.com/f">Fallback</a>')
        raise AssertionError(f"unexpected GET {url}")


def test_web_search_prefers_tavily_and_fetches_pages(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    tool = WebSearchTool(tavily_api_key="tvly", serp_api_key="serp")

    result = asyncio.run(tool.arun({"query": "agent tools"}))

    assert result["metadata"]["providers_used"] == ["tavily"]
    assert result["results"][0]["results"][0]["title"] == "T1"
    assert "page one" in result["results"][0]["results"][0]["page_content"]


def test_web_search_falls_back_to_serpapi(monkeypatch):
    class SerpOnlyClient(FakeAsyncClient):
        async def post(self, url, json=None):
            raise httpx.HTTPStatusError("down", request=None, response=None)

    monkeypatch.setattr(httpx, "AsyncClient", SerpOnlyClient)
    tool = WebSearchTool(tavily_api_key="tvly", serp_api_key="serp")

    result = asyncio.run(tool.arun({"query": "agent tools", "include_page_content": False}))

    assert result["metadata"]["providers_used"] == ["serpapi"]
    assert result["results"][0]["results"][0]["title"] == "S1"
