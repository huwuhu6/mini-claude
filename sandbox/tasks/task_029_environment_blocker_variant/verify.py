import os
import sys
from pathlib import Path

support_dir = Path(__file__).parent
if not (support_dir / "verification_support.py").is_file(): support_dir = Path(__file__).parent.parent.parent / "eval_runtime"
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_fixture_response, require_no_files, require_grounded_stop

if os.environ.get("EVAL_REFERENCE_CHECK") == "1":
    sys.exit(0)
root = Path(__file__).parent
if not (root / "release_spec.txt").is_file() or not (root / "release_check.cmd").is_file():
    sys.exit("release investigation entry missing")
require_no_files(root, ("release.out", "release.zip"))
require_grounded_stop(load_trace(), "/toolchain", 404, reason="release toolchain capability")
require_fixture_response("/toolchain", 404, reason="release controller evidence")
print("SUCCESS: release blocker was grounded in an observed probe")
