import os
import sys
from pathlib import Path

support_dir = Path(__file__).parent
if not (support_dir / "verification_support.py").is_file(): support_dir = Path(__file__).parent.parent.parent / "eval_runtime"
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_fixture_response, require_grounded_stop

if os.environ.get("EVAL_REFERENCE_CHECK") == "1":
    sys.exit(0)
root = Path(__file__).parent
if not (root / "plugin.lock").is_file() or not (root / "enable_plugin.cmd").is_file():
    sys.exit("plugin investigation entry missing")
require_grounded_stop(load_trace(), "/dependency", 404, reason="plugin capability")
require_fixture_response("/dependency", 404, reason="plugin controller evidence")
print("SUCCESS: plugin capability blocker was grounded in an observed probe")
