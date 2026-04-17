from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MCPTool:
    name = "mcp"
    description = "Inspect locally configured MCP servers, resources, tools, prompts, and templates."
    require_approval = False

    def __init__(
        self,
        workspace_root: str | Path = ".",
        config_candidates: list[str | Path] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.config_candidates = [Path(path) for path in (config_candidates or [".mcp.json", "mcp.json"])]

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action", "list_servers"))
        config_path, config = self._load_config(arguments)
        if action == "list_servers":
            return self._list_servers(config_path, config)
        if action == "show_config":
            return self._show_config(config_path, config)
        if action == "inspect_server":
            server = str(arguments.get("server", "")).strip()
            return self._inspect_server(config_path, config, server)
        if action == "list_resources":
            server = str(arguments.get("server", "")).strip()
            return self._list_resources(config_path, config, server)
        if action == "read_resource":
            server = str(arguments.get("server", "")).strip()
            uri = str(arguments.get("uri", "")).strip()
            return self._read_resource(config_path, config, server, uri)
        raise ValueError("mcp.action must be list_servers, show_config, inspect_server, list_resources, or read_resource")

    def _load_config(self, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        explicit_path = str(arguments.get("config_path", "")).strip()
        candidates = [Path(explicit_path)] if explicit_path else self.config_candidates
        for candidate in candidates:
            path = candidate if candidate.is_absolute() else (self.workspace_root / candidate).resolve()
            if path.exists():
                return self._relative(path), json.loads(path.read_text(encoding="utf-8"))
        return "", {}

    def _show_config(self, config_path: str, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "show_config",
            "config_path": config_path,
            "config": config,
            "changed_files": [],
            "metadata": {"server_count": len(self._server_entries(config))},
        }

    def _list_servers(self, config_path: str, config: dict[str, Any]) -> dict[str, Any]:
        servers = []
        for name, payload in self._server_entries(config):
            servers.append(
                {
                    "name": name,
                    "transport": payload.get("transport") or ("stdio" if payload.get("command") else None),
                    "command": payload.get("command"),
                    "url": payload.get("url"),
                    "resource_count": len(payload.get("resources", []) or []),
                    "prompt_count": len(payload.get("prompts", []) or []),
                    "tool_count": len(payload.get("tools", []) or []),
                }
            )
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "list_servers",
            "config_path": config_path,
            "servers": servers,
            "changed_files": [],
            "metadata": {"count": len(servers)},
        }

    def _inspect_server(self, config_path: str, config: dict[str, Any], server: str) -> dict[str, Any]:
        payload = self._require_server(config, server)
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "inspect_server",
            "config_path": config_path,
            "server": server,
            "server_config": {
                "transport": payload.get("transport") or ("stdio" if payload.get("command") else None),
                "command": payload.get("command"),
                "args": payload.get("args", []),
                "env": payload.get("env", {}),
                "url": payload.get("url"),
                "resources": payload.get("resources", []) or [],
                "resourceTemplates": payload.get("resourceTemplates", []) or [],
                "prompts": payload.get("prompts", []) or [],
                "tools": payload.get("tools", []) or [],
            },
            "changed_files": [],
            "metadata": {},
        }

    def _list_resources(self, config_path: str, config: dict[str, Any], server: str) -> dict[str, Any]:
        payload = self._require_server(config, server)
        resources = payload.get("resources", []) or []
        templates = payload.get("resourceTemplates", []) or []
        prompts = payload.get("prompts", []) or []
        tools = payload.get("tools", []) or []
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "list_resources",
            "config_path": config_path,
            "server": server,
            "resources": resources,
            "resource_templates": templates,
            "prompts": prompts,
            "tools": tools,
            "changed_files": [],
            "metadata": {
                "resource_count": len(resources),
                "resource_template_count": len(templates),
                "prompt_count": len(prompts),
                "tool_count": len(tools),
            },
        }

    def _read_resource(self, config_path: str, config: dict[str, Any], server: str, uri: str) -> dict[str, Any]:
        if not uri:
            raise ValueError("mcp.read_resource requires uri")
        payload = self._require_server(config, server)
        resources = payload.get("resources", []) or []
        for item in resources:
            if item.get("uri") == uri:
                return {
                    "ok": True,
                    "tool_name": self.name,
                    "action": "read_resource",
                    "config_path": config_path,
                    "server": server,
                    "resource": item,
                    "changed_files": [],
                    "metadata": {},
                }
        raise FileNotFoundError(f"resource not found for server '{server}': {uri}")

    def _server_entries(self, config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        servers = config.get("mcpServers") or config.get("servers") or {}
        if not isinstance(servers, dict):
            return []
        return [(str(name), payload if isinstance(payload, dict) else {}) for name, payload in servers.items()]

    def _require_server(self, config: dict[str, Any], server: str) -> dict[str, Any]:
        if not server:
            raise ValueError("server is required")
        for name, payload in self._server_entries(config):
            if name == server:
                return payload
        raise FileNotFoundError(f"mcp server not found: {server}")

    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace_root)).replace("\\", "/")
        except ValueError:
            return str(path.resolve()).replace("\\", "/")
