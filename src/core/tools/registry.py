"""Small registry that keeps each tool definition beside its handler."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any]


class ToolRegistry:
    """Stores exposed tool schemas and their execution handlers together."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def definitions(self) -> List[dict]:
        """Return tool definitions in the format consumed by ProviderToolDef."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for spec in self._tools.values()
        ]

    def has_handler(self, name: str) -> bool:
        return name in self._tools

    def execute(self, name: str, args: dict) -> Any:
        try:
            spec = self._tools[name]
        except KeyError:
            raise KeyError(f"Unknown tool: {name}") from None
        return spec.handler(**args)
