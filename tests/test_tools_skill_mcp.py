from __future__ import annotations

import asyncio
import json
from pathlib import Path

from packages.tools import MCPTool, SkillTool


def test_skill_tool_list_and_read(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Demo Skill\ncontent\n", encoding="utf-8")

    tool = SkillTool(workspace_root=tmp_path)

    listed = asyncio.run(tool.arun({"action": "list"}))
    assert listed["skills"][0]["name"] == "demo_skill"
    assert listed["skills"][0]["title"] == "Demo Skill"

    inspected = asyncio.run(tool.arun({"action": "inspect", "skill_name": "demo_skill"}))
    assert inspected["skill"]["description"] == "content"

    read = asyncio.run(tool.arun({"action": "read", "skill_name": "demo_skill"}))
    assert read["skill_name"] == "demo_skill"
    assert "# Demo Skill" in read["content"]
    assert read["metadata"]["title"] == "Demo Skill"


def test_mcp_tool_list_servers_and_read_resource(tmp_path: Path) -> None:
    config = {
        "mcpServers": {
            "docs": {
                "command": "uvx",
                "args": ["server.py"],
                "prompts": [{"name": "p1"}],
                "tools": [{"name": "t1"}],
                "resourceTemplates": [{"uriTemplate": "resource://{id}"}],
                "resources": [
                    {"uri": "resource://a", "name": "A", "content": "hello"},
                    {"uri": "resource://b", "name": "B", "content": "world"},
                ],
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(config), encoding="utf-8")

    tool = MCPTool(workspace_root=tmp_path)

    servers = asyncio.run(tool.arun({"action": "list_servers"}))
    assert servers["servers"][0]["name"] == "docs"
    assert servers["servers"][0]["tool_count"] == 1

    config_view = asyncio.run(tool.arun({"action": "show_config"}))
    assert config_view["config_path"] == ".mcp.json"

    inspected = asyncio.run(tool.arun({"action": "inspect_server", "server": "docs"}))
    assert inspected["server_config"]["args"] == ["server.py"]

    resources = asyncio.run(tool.arun({"action": "list_resources", "server": "docs"}))
    assert len(resources["resources"]) == 2
    assert len(resources["resource_templates"]) == 1
    assert len(resources["prompts"]) == 1
    assert len(resources["tools"]) == 1

    resource = asyncio.run(
        tool.arun({"action": "read_resource", "server": "docs", "uri": "resource://a"})
    )
    assert resource["resource"]["name"] == "A"
