from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx


def load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


class WebSearchTool:
    name = "web_search"
    description = "Search the web with Tavily first, SerpAPI second, and HTML crawl fallback."
    require_approval = False

    def __init__(
        self,
        tavily_api_key: str | None = None,
        serp_api_key: str | None = None,
        max_results: int = 5,
        request_timeout_s: float = 15.0,
        fetch_concurrency: int = 5,
    ) -> None:
        load_env_file()
        self.tavily_api_key = tavily_api_key or os.getenv("TAVIL_API_KEY") or os.getenv("TAVILY_API_KEY")
        self.serp_api_key = serp_api_key or os.getenv("SERP_API_KEY")
        self.max_results = max_results
        self.request_timeout_s = request_timeout_s
        self.fetch_concurrency = fetch_concurrency

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        queries = self._normalize_queries(arguments)
        include_snippets = bool(arguments.get("include_snippets", True))
        include_page_content = bool(arguments.get("include_page_content", True))

        async with httpx.AsyncClient(timeout=self.request_timeout_s, follow_redirects=True) as client:
            search_results = await asyncio.gather(
                *[self._search_one(client, query) for query in queries]
            )

            pages_by_query: list[list[dict[str, Any]]] = []
            if include_page_content:
                sem = asyncio.Semaphore(self.fetch_concurrency)
                fetch_tasks = []
                task_mapping: list[tuple[int, dict[str, Any]]] = []
                for idx, result in enumerate(search_results):
                    query_pages: list[dict[str, Any]] = []
                    pages_by_query.append(query_pages)
                    for item in result["results"][: self.max_results]:
                        task_mapping.append((idx, item))
                        fetch_tasks.append(self._fetch_page(sem, client, item["url"]))
                fetched = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                for (idx, item), page in zip(task_mapping, fetched):
                    if isinstance(page, Exception):
                        pages_by_query[idx].append({"url": item["url"], "content": "", "error": str(page)})
                    else:
                        pages_by_query[idx].append(page)
            else:
                pages_by_query = [[] for _ in search_results]

        aggregated = []
        for result, pages in zip(search_results, pages_by_query):
            entries = []
            for item in result["results"][: self.max_results]:
                entry = {"title": item["title"], "url": item["url"]}
                if include_snippets:
                    entry["snippet"] = item.get("snippet", "")
                matched_page = next((page for page in pages if page["url"] == item["url"]), None)
                if matched_page:
                    entry["page_content"] = matched_page.get("content", "")
                entries.append(entry)
            aggregated.append(
                {
                    "query": result["query"],
                    "provider": result["provider"],
                    "results": entries,
                }
            )

        return {
            "ok": True,
            "tool_name": self.name,
            "queries": queries,
            "results": aggregated,
            "changed_files": [],
            "metadata": {"providers_used": [item["provider"] for item in search_results]},
        }

    def _normalize_queries(self, arguments: dict[str, Any]) -> list[str]:
        if "queries" in arguments:
            raw_queries = arguments["queries"]
            if not isinstance(raw_queries, list) or not raw_queries:
                raise ValueError("queries must be a non-empty list")
            queries = [str(item).strip() for item in raw_queries if str(item).strip()]
        else:
            query = str(arguments.get("query", "")).strip()
            if not query:
                raise ValueError("query is required")
            queries = [query]
        return queries

    async def _search_one(self, client: httpx.AsyncClient, query: str) -> dict[str, Any]:
        if self.tavily_api_key:
            try:
                return await self._search_tavily(client, query)
            except Exception:
                pass
        if self.serp_api_key:
            try:
                return await self._search_serpapi(client, query)
            except Exception:
                pass
        return await self._fallback_html_search(client, query)

    async def _search_tavily(self, client: httpx.AsyncClient, query: str) -> dict[str, Any]:
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self.tavily_api_key,
                "query": query,
                "max_results": self.max_results,
                "search_depth": "advanced",
            },
        )
        response.raise_for_status()
        data = response.json()
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
            }
            for item in data.get("results", [])
            if item.get("url")
        ]
        return {"query": query, "provider": "tavily", "results": results}

    async def _search_serpapi(self, client: httpx.AsyncClient, query: str) -> dict[str, Any]:
        response = await client.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google",
                "q": query,
                "api_key": self.serp_api_key,
                "num": self.max_results,
            },
        )
        response.raise_for_status()
        data = response.json()
        organic_results = data.get("organic_results", [])
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in organic_results
            if item.get("link")
        ]
        return {"query": query, "provider": "serpapi", "results": results}

    async def _fallback_html_search(self, client: httpx.AsyncClient, query: str) -> dict[str, Any]:
        response = await client.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        html = response.text
        results = []
        for part in html.split('class="result__a"')[1 : self.max_results + 1]:
            href_marker = 'href="'
            title_start = part.find(">")
            title_end = part.find("</a>")
            href_start = part.find(href_marker)
            if href_start == -1 or title_start == -1 or title_end == -1:
                continue
            href_start += len(href_marker)
            href_end = part.find('"', href_start)
            url = part[href_start:href_end]
            title = part[title_start + 1 : title_end]
            results.append({"title": title, "url": url, "snippet": ""})
        return {"query": query, "provider": "html_fallback", "results": results}

    async def _fetch_page(self, sem: asyncio.Semaphore, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        async with sem:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            text = self._strip_html(response.text)
            return {"url": url, "content": text[:4000]}

    def _strip_html(self, html: str) -> str:
        text = html
        for token in ("<script", "<style"):
            while True:
                start = text.lower().find(token)
                if start == -1:
                    break
                end = text.lower().find("</script>" if token == "<script" else "</style>", start)
                if end == -1:
                    text = text[:start]
                    break
                end += len("</script>" if token == "<script" else "</style>")
                text = text[:start] + text[end:]
        output = []
        inside = False
        for char in text:
            if char == "<":
                inside = True
                continue
            if char == ">":
                inside = False
                output.append(" ")
                continue
            if not inside:
                output.append(char)
        return " ".join("".join(output).split())
