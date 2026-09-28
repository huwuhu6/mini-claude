import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.tracing.manager import TraceManager
from models.config import ConfigManager, apply_runtime_overrides
from eval_runner import _effective_config_metadata


def test_runtime_overrides_are_ephemeral_and_record_effective_values():
    config_path = ROOT / "configs" / "default.yaml"
    original = config_path.read_text(encoding="utf-8")
    config = ConfigManager(config_path).get_config()

    apply_runtime_overrides(config, {
        "context_window_tokens": 1_000_000,
        "microcompact_token_threshold": 1_000,
        "full_compression_token_threshold": 2_000,
        "memory": True,
    })

    assert config.compression.microcompact_token_threshold == 1_000
    assert config.compression.full_compression_token_threshold == 2_000
    assert config.features.memory is True
    assert config_path.read_text(encoding="utf-8") == original

    effective = _effective_config_metadata({
        "microcompact_token_threshold": 1_000,
        "full_compression_token_threshold": 2_000,
        "memory": False,
    })
    assert effective["context_window_tokens"] == 1_000_000
    assert effective["microcompact_token_threshold"] == 1_000
    assert effective["full_compression_token_threshold"] == 2_000
    assert effective["memory"] is False
    assert {"provider", "model", "max_tokens", "temperature"} <= effective.keys()


def test_invalid_runtime_threshold_relationship_is_rejected():
    config = ConfigManager(ROOT / "configs" / "default.yaml").get_config()

    with pytest.raises(ValueError, match="less than"):
        apply_runtime_overrides(config, {
            "microcompact_token_threshold": 2_000,
            "full_compression_token_threshold": 1_000,
        })

    with pytest.raises(ValueError, match="must not exceed"):
        apply_runtime_overrides(config, {
            "context_window_tokens": 1_000,
            "microcompact_token_threshold": 500,
            "full_compression_token_threshold": 2_000,
        })


def test_trace_persists_effective_config_and_compression_qualification(tmp_path):
    trace = TraceManager(trace_dir=tmp_path)
    trace.start_task(
        task_id="qualification",
        effective_config={
            "provider": "dashscope",
            "model": "deepseek-v4-flash-0731",
            "context_window_tokens": 1_000_000,
            "microcompact_token_threshold": 1_000,
            "full_compression_token_threshold": 2_000,
            "memory": False,
            "max_tokens": 8_000,
            "temperature": 0.7,
        },
    )
    trace.start_turn(0)
    trace.record_compression_observation("full", 24, 9, 1)
    trace.record_compression()
    trace.record_provider_usage(
        {"prompt_tokens": 2_100, "completion_tokens": 20, "total_tokens": 2_120},
        estimated_prompt_tokens=2_000,
    )
    output = trace.end_task("SUCCESS")

    data = json.loads(Path(output).read_text(encoding="utf-8"))
    assert data["effective_config"]["memory"] is False
    turn = data["turns"][0]
    assert turn["estimated_prompt_tokens"] == 2_000
    assert turn["compression_triggered"] is True
    assert turn["compression_type"] == "full"
    assert turn["compression_message_count_before"] == 24
    assert turn["compression_message_count_after"] == 9
    assert turn["retained_read_file_results"] == 1
