"""
Feature Management System - Pluggable feature management with dependency support.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Callable

logger = logging.getLogger(__name__)


@dataclass
class FeatureDependency:
    feature: str
    required: bool = True  # True = must be enabled, False = must be disabled


@dataclass
class FeatureDefinition:
    name: str
    description: str = ""
    enabled: bool = True
    dependencies: List[FeatureDependency] = field(default_factory=list)
    category: str = "general"


class FeatureManager:
    """Manages feature flags, tool filtering, and dependency resolution."""

    def __init__(self):
        self._features: Dict[str, FeatureDefinition] = {}
        self._tool_feature_map: Dict[str, str] = {}  # tool_name -> feature_name
        self._change_listeners: List[Callable] = []

    # ── Feature Registration ──────────────────────────────────

    def register_feature(self, feature: FeatureDefinition) -> None:
        if feature.name in self._features:
            logger.warning(f"覆盖已有功能: {feature.name}")
        self._features[feature.name] = feature
        logger.info(f"已注册功能: {feature.name} (启用={feature.enabled})")

    def register_tool_for_feature(self, tool_name: str, feature_name: str) -> None:
        if feature_name not in self._features:
            raise ValueError(f"Unknown feature: {feature_name}")
        self._tool_feature_map[tool_name] = feature_name

    # ── Feature State ─────────────────────────────────────────

    def is_enabled(self, name: str) -> bool:
        feat = self._features.get(name)
        if feat is None:
            return False
        if not feat.enabled:
            return False
        # Check dependencies
        for dep in feat.dependencies:
            dep_feat = self._features.get(dep.feature)
            if dep_feat is None:
                if dep.required:
                    return False
                continue
            if dep.required and not dep_feat.enabled:
                return False
            if not dep.required and dep_feat.enabled:
                return False
        return True

    def enable(self, name: str) -> bool:
        feat = self._features.get(name)
        if feat is None:
            logger.warning(f"无法启用未知功能: {name}")
            return False
        if not self._can_enable(name):
            logger.warning(f"无法启用 {name}: 依赖条件未满足")
            return False
        feat.enabled = True
        self._notify(name, True)
        logger.info(f"功能已启用: {name}")
        return True

    def disable(self, name: str) -> bool:
        feat = self._features.get(name)
        if feat is None:
            logger.warning(f"无法禁用未知功能: {name}")
            return False
        feat.enabled = False
        # Also disable dependents
        for other in list(self._features.values()):
            for dep in other.dependencies:
                if dep.feature == name and dep.required:
                    self.disable(other.name)
        self._notify(name, False)
        logger.info(f"功能已禁用: {name}")
        return True

    def _can_enable(self, name: str, visited: Optional[Set[str]] = None) -> bool:
        feat = self._features.get(name)
        if feat is None:
            return False
        if visited is None:
            visited = set()
        if name in visited:
            return True  # circular dep, assume ok
        visited.add(name)
        for dep in feat.dependencies:
            dep_feat = self._features.get(dep.feature)
            if dep_feat is None:
                if dep.required:
                    return False
                continue
            state_ok = dep_feat.enabled if dep.required else not dep_feat.enabled
            if not state_ok:
                return False
            if not self._can_enable(dep.feature, visited):
                return False
        return True

    # ── Tool Filtering ────────────────────────────────────────

    def filter_tools(self, tools: List[Dict]) -> List[Dict]:
        """Filter tools based on which features are enabled."""
        filtered = []
        for tool in tools:
            tool_name = tool.get('name', '')
            req_feature = self._tool_feature_map.get(tool_name)
            if req_feature is None or self.is_enabled(req_feature):
                filtered.append(tool)
        return filtered

    # ── Dependency Graph ──────────────────────────────────────

    def get_dependency_graph(self) -> Dict[str, List[str]]:
        graph: Dict[str, List[str]] = {}
        for name, feat in self._features.items():
            graph[name] = [d.feature for d in feat.dependencies]
        return graph

    def get_topological_order(self) -> List[str]:
        """Return feature names in topological order (dependencies first)."""
        graph = self.get_dependency_graph()
        visited: Set[str] = set()
        result: List[str] = []

        def dfs(node: str):
            if node in visited:
                return
            visited.add(node)
            for dep in graph.get(node, []):
                dfs(dep)
            result.append(node)

        for name in self._features:
            if name not in visited:
                dfs(name)
        return result

    # ── Listeners ─────────────────────────────────────────────

    def on_change(self, callback: Callable[[str, bool], None]) -> None:
        self._change_listeners.append(callback)

    def _notify(self, feature_name: str, enabled: bool) -> None:
        for cb in self._change_listeners:
            try:
                cb(feature_name, enabled)
            except Exception as e:
                logger.error(f"功能变更监听器失败: {e}")

    # ── Query ─────────────────────────────────────────────────

    def list_features(self) -> List[FeatureDefinition]:
        return list(self._features.values())

    def get_feature(self, name: str) -> Optional[FeatureDefinition]:
        return self._features.get(name)

    def get_enabled_features(self) -> List[str]:
        return [n for n in self._features if self.is_enabled(n)]

    def update_from_config(self, feature_flags: Dict[str, bool]) -> None:
        for name, enabled in feature_flags.items():
            feat = self._features.get(name)
            if feat:
                if enabled:
                    self.enable(name)
                else:
                    self.disable(name)
