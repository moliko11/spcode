from __future__ import annotations

import asyncio

from packages.tools import ToolSearchTool


CATALOG = [
    {
        "name": "file_read",
        "description": "Read files from the workspace",
        "category": "workspace",
        "tags": ["file", "read", "workspace"],
        "default_loaded": True,
        "requires_approval": False,
    },
    {
        "name": "skill",
        "description": "Discover and inspect local skills",
        "category": "meta",
        "tags": ["skill", "workflow", "prompt"],
        "default_loaded": False,
        "requires_approval": False,
    },
    {
        "name": "mcp",
        "description": "Inspect MCP servers and resources",
        "category": "integration",
        "tags": ["mcp", "server", "resource", "tool"],
        "default_loaded": False,
        "requires_approval": False,
    },
]


def test_tool_search_recommends_hidden_tools() -> None:
    tool = ToolSearchTool(catalog=CATALOG)
    result = asyncio.run(
        tool.arun(
            {
                "intent": "inspect local skill workflows",
                "keywords": ["skill", "workflow"],
                "only_not_loaded": True,
                "current_loaded_tools": ["file_read"],
            }
        )
    )
    assert result["recommended_tools"][0]["name"] == "skill"
    assert result["recommended_tools"][0]["load"] is True
    assert result["already_loaded"] == ["file_read"]


def test_tool_search_list_respects_only_not_loaded() -> None:
    tool = ToolSearchTool(catalog=CATALOG)
    result = asyncio.run(
        tool.arun(
            {
                "action": "list",
                "only_not_loaded": True,
                "current_loaded_tools": ["file_read", "mcp"],
            }
        )
    )
    assert [item["name"] for item in result["recommended_tools"]] == ["skill"]
