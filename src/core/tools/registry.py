"""Small registry that keeps each tool definition beside its handler."""
from __future__ import annotations

from dataclasses import dataclass
from inspect import signature
from typing import Any, Callable, Dict, List


class ToolArgumentBindingError(TypeError):
    """Arguments rejected before entering a registered handler."""


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

    def execution_contract(self) -> dict:
        """Data-only schema/implementation identity for strict snapshot compatibility."""
        import hashlib
        from inspect import getsource
        contracts = {}
        for name, spec in self._tools.items():
            try:
                source = getsource(spec.handler)
            except (OSError, TypeError) as exc:
                raise ValueError(f"工具实现无法验证快照兼容性: {name}") from exc
            contracts[name] = {
                "schema": spec.input_schema,
                "handler": f"{spec.handler.__module__}.{spec.handler.__qualname__}",
                "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            }
        return contracts

    def has_handler(self, name: str) -> bool:
        return name in self._tools

    def execute(self, name: str, args: dict) -> Any:
        try:
            spec = self._tools[name]
        except KeyError:
            raise KeyError(f"Unknown tool: {name}") from None
        try:
            handler_signature = signature(spec.handler)
        except (TypeError, ValueError):
            # Some native callables have no inspectable signature. Do not infer
            # argument rejection from a TypeError raised during their execution.
            handler_signature = None
        if handler_signature is not None:
            try:
                handler_signature.bind(**args)
            except TypeError as exc:
                raise ToolArgumentBindingError(str(exc)) from exc
        return spec.handler(**args)
