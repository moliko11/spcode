from __future__ import annotations

import re
from typing import Any


class ToolSearchTool:
    name = "tool_search"
    description = "Discover available tools and recommend which ones fit the current task."
    require_approval = False

    def __init__(self, catalog: list[dict[str, Any]] | None = None) -> None:
        self.catalog = [self._normalize_entry(item) for item in (catalog or [])]

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action", "search")).strip().lower()
        if action == "list":
            return self._list_tools(arguments)
        if action == "search":
            return self._search_tools(arguments)
        raise ValueError("tool_search.action must be search or list")

    def _list_tools(self, arguments: dict[str, Any]) -> dict[str, Any]:
        only_not_loaded = bool(arguments.get("only_not_loaded", False))
        current_loaded = self._normalize_name_list(arguments.get("current_loaded_tools"))
        tools = [
            self._render_entry(item)
            for item in self.catalog
            if not (only_not_loaded and item["name"] in current_loaded)
        ]
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "list",
            "recommended_tools": tools,
            "already_loaded": sorted(current_loaded),
            "hidden_tools_count": sum(1 for item in self.catalog if not item["default_loaded"]),
            "changed_files": [],
            "metadata": {"count": len(tools)},
        }

    def _search_tools(self, arguments: dict[str, Any]) -> dict[str, Any]:
        intent = str(arguments.get("intent", "")).strip()
        keywords = self._normalize_name_list(arguments.get("keywords"))
        current_loaded = self._normalize_name_list(arguments.get("current_loaded_tools"))
        only_not_loaded = bool(arguments.get("only_not_loaded", False))
        max_results = int(arguments.get("max_results", 8))
        tokens = self._tokenize(" ".join([intent, *keywords]))

        ranked: list[tuple[int, dict[str, Any], list[str]]] = []
        for item in self.catalog:
            if only_not_loaded and item["name"] in current_loaded:
                continue
            score, reasons = self._score_entry(item, tokens)
            if not tokens:
                if item["default_loaded"]:
                    score += 1
                    reasons.append("part of the default core toolset")
                else:
                    score += 2
                    reasons.append("available as an on-demand tool")
            if score <= 0:
                continue
            ranked.append((score, item, reasons))

        ranked.sort(
            key=lambda entry: (
                -entry[0],
                entry[1]["default_loaded"],
                entry[1]["requires_approval"],
                entry[1]["name"],
            )
        )

        results = []
        for score, item, reasons in ranked[:max_results]:
            rendered = self._render_entry(item)
            rendered["why"] = "; ".join(dict.fromkeys(reasons))[:280]
            rendered["score"] = score
            results.append(rendered)

        return {
            "ok": True,
            "tool_name": self.name,
            "action": "search",
            "intent": intent,
            "keywords": keywords,
            "recommended_tools": results,
            "already_loaded": sorted(current_loaded),
            "hidden_tools_count": sum(1 for item in self.catalog if not item["default_loaded"]),
            "changed_files": [],
            "metadata": {"count": len(results)},
        }

    def _score_entry(self, item: dict[str, Any], tokens: list[str]) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []
        haystacks = {
            "name": item["name"],
            "description": item["description"],
            "category": item["category"],
            "tags": " ".join(item["tags"]),
        }
        for token in tokens:
            if token == item["name"]:
                score += 7
                reasons.append(f"matches tool name '{item['name']}'")
            elif token in item["name"]:
                score += 5
                reasons.append(f"closely matches tool name '{item['name']}'")

            if token in haystacks["category"]:
                score += 3
                reasons.append(f"matches category '{item['category']}'")
            if token in haystacks["tags"]:
                score += 3
                reasons.append(f"matches tags for {item['name']}")
            if token in haystacks["description"]:
                score += 2
                reasons.append(f"matches description of {item['name']}")

        if score > 0 and not item["default_loaded"]:
            score += 1
            reasons.append("must be loaded on demand")
        return score, reasons

    def _render_entry(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": item["name"],
            "description": item["description"],
            "category": item["category"],
            "default_loaded": item["default_loaded"],
            "requires_approval": item["requires_approval"],
            "load": not item["default_loaded"],
        }

    def _normalize_entry(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": str(item.get("name", "")).strip(),
            "description": str(item.get("description", "")).strip(),
            "category": str(item.get("category", "general")).strip().lower(),
            "tags": self._normalize_name_list(item.get("tags")),
            "default_loaded": bool(item.get("default_loaded", False)),
            "requires_approval": bool(item.get("requires_approval", False)),
        }

    def _normalize_name_list(self, raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        result = []
        for item in raw:
            value = str(item).strip().lower()
            if value:
                result.append(value)
        return result

    def _tokenize(self, text: str) -> list[str]:
        return [token for token in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if token]
