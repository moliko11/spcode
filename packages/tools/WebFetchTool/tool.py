from __future__ import annotations

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlparse

import httpx

# Hostnames that are always blocked regardless of DNS resolution
_BLOCKED_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.internal",
})

# Private / link-local / loopback networks blocked for raw IP addresses
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / AWS metadata
    ipaddress.ip_network("100.64.0.0/10"),    # carrier-grade NAT
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _validate_url(url: str) -> None:
    """Raise ValueError if the URL is disallowed (SSRF prevention).

    Blocks:
    - Non-http/https schemes
    - Known internal hostnames (localhost, metadata servers)
    - Raw private/loopback/link-local IP addresses

    Note: hostname-based DNS rebinding cannot be blocked here without
    a synchronous DNS lookup; rely on network-level controls for that.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"disallowed URL scheme {scheme!r}; only http/https allowed")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("URL has no hostname")
    if host in _BLOCKED_HOSTNAMES:
        raise ValueError(f"blocked hostname: {host!r}")
    # If the host is a raw IP address, validate it directly (no DNS needed)
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_link_local or ip.is_private:
            raise ValueError(f"blocked IP address: {ip}")
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise ValueError(f"blocked IP address {ip} (matches {net})")
    except ValueError as exc:
        if "blocked" in str(exc):
            raise
        # Not a raw IP address — hostname will be resolved by DNS at fetch time.
        # We accept this; network-level egress controls should handle DNS rebinding.


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
        for u in urls:
            _validate_url(u)
        return urls

    async def _fetch_one(self, sem: asyncio.Semaphore, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        async with sem:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            # Validate redirect destination to prevent open-redirect SSRF
            final_url = str(response.url)
            if final_url != url:
                _validate_url(final_url)
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
