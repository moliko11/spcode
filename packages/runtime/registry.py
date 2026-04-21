from __future__ import annotations

from typing import Any, Protocol

from .models import ToolSpec


class BaseTool(Protocol):
    """
    基础工具接口
    """
    async def arun(self, arguments: dict[str, Any]) -> Any:
        ...


class ToolRegistry:
    """
    工具注册器
    """
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._tools: dict[str, BaseTool] = {}

    def register(self, spec: ToolSpec, tool: BaseTool) -> None:
        self._specs[spec.name] = spec
        self._tools[spec.name] = tool

    def get_spec(self, name: str) -> ToolSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise ValueError(f"unknown tool spec: {name}")
        return spec

    def get_tool(self, name: str) -> BaseTool:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool implementation: {name}")
        return tool

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def openai_tools(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        allowed = set(names) if names is not None else None
        tools = []
        for spec in self._specs.values():
            if allowed is not None and spec.name not in allowed:
                continue
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
            )
        return tools
