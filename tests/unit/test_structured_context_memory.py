import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.context_memory import StructuredContextMemory


def test_recent_files_follow_lru_order_and_evict_oldest():
    memory = StructuredContextMemory(max_recent_files=2)

    memory.remember_file("a.py", "a1")
    memory.remember_file("b.py", "b1")
    memory.remember_file("a.py", "a1")
    memory.remember_file("c.py", "c1")

    assert memory.recent_files == ("a.py", "c.py")


def test_path_aliases_share_one_memory_identity():
    memory = StructuredContextMemory()

    memory.record_observation("src/../app.py", 1, 5, "first", "v1")
    memory.record_observation("./app.py", 1, 5, "updated", "v1")

    assert memory.recent_files == ("app.py",)
    assert len(memory.observations) == 1
    assert memory.get_observation("app.py", 1, 5, "v1").observation == "updated"


def test_observations_keep_ranges_separate_for_same_path():
    memory = StructuredContextMemory()
    memory.record_observation("src/app.py", 1, 20, "imports", "v1")
    memory.record_observation("src/app.py", 21, 40, "handlers", "v1")

    assert len(memory.observations) == 2
    assert memory.get_observation("src/app.py", 1, 20, "v1").observation == "imports"
    assert memory.get_observation("src/app.py", 21, 40, "v1").observation == "handlers"


def test_same_path_and_range_updates_in_place():
    clock_values = iter((10.0, 20.0))
    memory = StructuredContextMemory(clock=lambda: next(clock_values))
    original = memory.record_observation("app.py", 5, 10, "old", "v1")
    updated = memory.record_observation("app.py", 5, 10, "new", "v1")

    assert len(memory.observations) == 1
    assert original.updated_order < updated.updated_order
    assert updated.created_at == 20.0
    assert memory.get_observation("app.py", 5, 10, "v1").observation == "new"


def test_changed_freshness_invalidates_old_observations():
    memory = StructuredContextMemory()
    memory.record_observation("app.py", 1, 5, "old implementation", "hash-v1")

    assert memory.refresh_freshness("app.py", "hash-v2") is True
    assert memory.get_observation("app.py", 1, 5, "hash-v1") is None
    assert memory.observations == ()


def test_invalidate_removes_path_observations_but_keeps_recent_file():
    memory = StructuredContextMemory()
    memory.record_observation("app.py", 1, 5, "old implementation", "hash-v1")

    memory.invalidate("app.py")

    assert memory.observations == ()
    assert memory.recent_files == ("app.py",)


def test_render_is_bounded_and_never_claims_a_range_is_a_whole_file():
    memory = StructuredContextMemory(max_render_chars=120)
    memory.record_observation("src/app.py", 41, 60, "x" * 1000, "1234567890abcdef")

    rendered = memory.render()

    assert len(rendered) <= 120
    assert "src/app.py lines 41-60" in rendered
    assert "whole file" not in rendered.lower()
    assert "…" in rendered


def test_empty_memory_renders_no_transient_payload():
    assert StructuredContextMemory().render() == ""
