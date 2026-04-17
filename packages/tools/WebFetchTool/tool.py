from __future__ import annotations

import asyncio
from typing import Any

import httpx


class WebFetchTool:
    name = "web_fetch"
    description = "Fetch one or more URLs concurrently and extract simplified page text."
    require_approval = False

    def __init__(self, request_timeout_s: float = 15.0, fetch_concurrency: int = 5) -> None:
        self.request_timeout_s = request_timeout_s
        self.fetch_concurrency = fetch_concurrency

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        urls = self._normalize_urls(arguments)
        sem = asyncio.Semaphore(self.fetch_concurrency)

        async with httpx.AsyncClient(timeout=self.request_timeout_s, follow_redirects=True) as client:
            results = await asyncio.gather(
                *[self._fetch_one(sem, client, url) for url in urls],
                return_exceptions=True,
            )

        pages = []
        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                pages.append({"url": url, "content": "", "error": str(result)})
            else:
                pages.append(result)

        return {
            "ok": True,
            "tool_name": self.name,
            "urls": urls,
            "results": pages,
            "changed_files": [],
            "metadata": {"count": len(urls)},
        }

    def _normalize_urls(self, arguments: dict[str, Any]) -> list[str]:
        if "urls" in arguments:
            raw_urls = arguments["urls"]
            if not isinstance(raw_urls, list) or not raw_urls:
                raise ValueError("urls must be a non-empty list")
            urls = [str(item).strip() for item in raw_urls if str(item).strip()]
        else:
            url = str(arguments.get("url", "")).strip()
            if not url:
                raise ValueError("url is required")
            urls = [url]
        return urls

    async def _fetch_one(self, sem: asyncio.Semaphore, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        async with sem:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            return {"url": url, "content": self._strip_html(response.text)[:4000]}

    def _strip_html(self, html: str) -> str:
        output = []
        inside = False
        for char in html:
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
