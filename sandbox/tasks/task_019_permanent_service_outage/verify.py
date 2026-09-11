import os
import sys
from pathlib import Path

support_dir = Path(__file__).parent
if not (support_dir / "verification_support.py").is_file(): support_dir = Path(__file__).parent.parent.parent / "eval_runtime"
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_fixture_response, require_grounded_stop

if os.environ.get("EVAL_REFERENCE_CHECK") == "1":
    sys.exit(0)
require_grounded_stop(load_trace(), "/health", 503, reason="service availability")
require_fixture_response("/health", 503, reason="service controller evidence")
print("SUCCESS: permanent service blocker reported")
