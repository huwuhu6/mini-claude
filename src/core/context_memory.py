"""Small, deterministic, file-scoped memory for the main agent.

This module deliberately stores only bounded facts produced by successful file
tools.  It has no LLM dependency and never owns conversation messages.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import posixpath
from time import time
from typing import Callable, Optional


MAX_RECENT_FILES = 8
MAX_OBSERVATIONS = 16
MAX_OBSERVATION_CHARS = 240
MAX_RENDER_CHARS = 1_600


@dataclass(frozen=True)
class FileObservation:
    """A bounded note about one explicitly-read range of a file."""

    path: str
    start_line: int
    end_line: int
    observation: str
    freshness: str
    created_at: float
    updated_order: int


class StructuredContextMemory:
    """Bounded LRU file memory with freshness-aware range observations."""

    def __init__(
        self,
        *,
        max_recent_files: int = MAX_RECENT_FILES,
        max_observations: int = MAX_OBSERVATIONS,
        max_render_chars: int = MAX_RENDER_CHARS,
        clock: Callable[[], float] = time,
    ) -> None:
        if max_recent_files <= 0 or max_observations <= 0 or max_render_chars <= 0:
            raise ValueError("Structured memory limits must be positive")
        self.max_recent_files = max_recent_files
        self.max_observations = max_observations
        self.max_render_chars = min(max_render_chars, MAX_RENDER_CHARS)
        self._clock = clock
        self._recent_files: OrderedDict[str, Optional[str]] = OrderedDict()
        self._observations: dict[tuple[str, int, int], FileObservation] = {}
        self._update_order = 0

    @staticmethod
    def _path_key(path: str) -> str:
        raw_path = str(path).strip().replace("\\", "/")
        if not raw_path:
            raise ValueError("Memory path must not be empty")
        key = posixpath.normpath(raw_path)
        if key in {"", "."}:
            raise ValueError("Memory path must not be empty")
        return key

    @staticmethod
    def _short_text(text: str) -> str:
        compact = " ".join(str(text).split())
        if len(compact) <= MAX_OBSERVATION_CHARS:
            return compact
        return compact[: MAX_OBSERVATION_CHARS - 1].rstrip() + "…"

    @property
    def recent_files(self) -> tuple[str, ...]:
        """Paths from least to most recently observed/touched."""
        return tuple(self._recent_files.keys())

    @property
    def observations(self) -> tuple[FileObservation, ...]:
        """Observations ordered by their most recent update."""
        return tuple(sorted(self._observations.values(), key=lambda item: item.updated_order))

    def remember_file(self, path: str, freshness: Optional[str] = None) -> None:
        """Touch a file and invalidate its observations when its hash changed."""
        key = self._path_key(path)
        previous = self._recent_files.get(key)
        normalized_freshness = str(freshness) if freshness is not None else None
        if previous is not None and normalized_freshness is not None and previous != normalized_freshness:
            self._drop_observations_for_path(key)
        self._recent_files[key] = normalized_freshness
        self._recent_files.move_to_end(key)
        while len(self._recent_files) > self.max_recent_files:
            evicted, _ = self._recent_files.popitem(last=False)
            self._drop_observations_for_path(evicted)

    def record_observation(
        self,
        path: str,
        start_line: int,
        end_line: int,
        observation: str,
        freshness: str,
    ) -> FileObservation:
        """Insert or replace the note for one exact file range."""
        start = int(start_line)
        end = int(end_line)
        if start <= 0 or end < start:
            raise ValueError("Observation range must be positive and ordered")
        key = self._path_key(path)
        fingerprint = str(freshness)
        if not fingerprint:
            raise ValueError("Observation freshness must not be empty")

        self.remember_file(key, fingerprint)
        self._update_order += 1
        observation_key = (key, start, end)
        item = FileObservation(
            path=key,
            start_line=start,
            end_line=end,
            observation=self._short_text(observation),
            freshness=fingerprint,
            created_at=self._clock(),
            updated_order=self._update_order,
        )
        self._observations[observation_key] = item
        self._trim_observations()
        return item

    def get_observation(
        self,
        path: str,
        start_line: int,
        end_line: int,
        freshness: str,
    ) -> Optional[FileObservation]:
        """Return an exact-range observation only if its current hash matches."""
        key = self._path_key(path)
        item = self._observations.get((key, int(start_line), int(end_line)))
        if item is None or item.freshness != str(freshness):
            return None
        return item

    def refresh_freshness(self, path: str, freshness: Optional[str]) -> bool:
        """Update a known file hash and report whether stale observations were dropped."""
        key = self._path_key(path)
        before = len(self._observations)
        self.remember_file(key, freshness)
        return len(self._observations) != before

    def invalidate(self, path: str) -> None:
        """Forget all observations for a path while retaining its LRU recency."""
        key = self._path_key(path)
        self._drop_observations_for_path(key)
        if key in self._recent_files:
            self._recent_files[key] = None
            self._recent_files.move_to_end(key)

    def render(self, max_chars: Optional[int] = None) -> str:
        """Return a strictly bounded, range-explicit transient context payload."""
        budget = self.max_render_chars if max_chars is None else min(
            max(0, int(max_chars)), self.max_render_chars,
        )
        if budget == 0:
            return ""
        if not self._recent_files and not self._observations:
            return ""

        lines: list[str] = ["Recent files: " + (", ".join(reversed(self.recent_files)) or "none")]
        for item in sorted(self._observations.values(), key=lambda entry: entry.updated_order, reverse=True):
            line = (
                f"- {item.path} lines {item.start_line}-{item.end_line} "
                f"(freshness {item.freshness[:12]}): {item.observation}"
            )
            candidate = "\n".join(lines + [line])
            if len(candidate) > budget:
                remaining = budget - len("\n".join(lines)) - 1
                if remaining > 1:
                    lines.append(line[: remaining - 1].rstrip() + "…")
                break
            lines.append(line)

        rendered = "\n".join(lines)
        return rendered[:budget]

    def _drop_observations_for_path(self, path: str) -> None:
        for key in [key for key in self._observations if key[0] == path]:
            del self._observations[key]

    def _trim_observations(self) -> None:
        overflow = len(self._observations) - self.max_observations
        if overflow <= 0:
            return
        oldest = sorted(self._observations, key=lambda key: self._observations[key].updated_order)
        for key in oldest[:overflow]:
            del self._observations[key]
