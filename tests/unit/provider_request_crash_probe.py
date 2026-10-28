"""Block the third fake request so the parent can kill a real Agent process."""
import json
from pathlib import Path
import sys
import time

from agent import mini_claude_agent as module
from core.features import FeatureDefinition
from core.runtime_context.preflight import PreflightResult
from core.tools.base_tools import ToolResult
from core.tools.registry import ToolSpec
from test_run_snapshot import MockProvider, calls
from test_output_limit_recovery import truncated, wire


def main(base):
    workspace = base / 'workspace'
    module.run_preflight = lambda root: PreflightResult('OFFLINE', workspace_root=str(root), workspace_read_write=True)
    module.MiniClaudeAgent._setup_logging = lambda self: None
    module.BackgroundProcessor.start = lambda self: None
    module.configure_primary_provider = lambda *args: None
    a = module.MiniClaudeAgent(config_path=base / 'config.yaml', workspace_root=workspace,
                              workspace_confirmed=True, runtime_data_root=base / 'runtime')
    a.config.llm.output_limit_recovery.persist_effort_for_run = True
    a.tool_registry.register(ToolSpec('probe', 'Offline tool',
        {'type': 'object', 'properties': {'label': {'type': 'string'}}, 'required': ['label']},
        lambda label: ToolResult('actual ' + label)))
    a.feature_manager.register_feature(FeatureDefinition('probe_feature'))
    a.feature_manager.register_tool_for_feature('probe', 'probe_feature')
    provider = MockProvider([truncated(), {'tool_calls': calls('B'), 'finish_reason': 'tool_calls',
        'usage': {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25}}])
    wire(provider)
    original = provider.create_message

    def request(*args, **kwargs):
        if len(provider.requests) >= 2:
            (base / 'ready.json').write_text(json.dumps({'session_path': str(a.session_recorder.path)}), encoding='utf-8')
            while True:
                time.sleep(1)
        return original(*args, **kwargs)

    provider.create_message = request
    a.provider_manager.register_provider('offline', provider, is_primary=True)
    a.run('exercise truncation recovery')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
