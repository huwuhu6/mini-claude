import os
import sys
from pathlib import Path

support_dir = Path(__file__).parent
if not (support_dir / "verification_support.py").is_file(): support_dir = Path(__file__).parent.parent.parent / "eval_runtime"
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_fixture_states, require_state_observations, require_grounded_stop

if os.environ.get("EVAL_REFERENCE_CHECK") == "1":
    sys.exit(0)
trace = load_trace()
require_grounded_stop(trace, "/state", 200, reason="process state oscillation")
require_state_observations(trace, ("state_check.cmd", "/state", "business_state"), minimum=2)
require_fixture_states(("A", "B"), minimum=2, reason="process state controller evidence")
print("SUCCESS: process state oscillation was grounded in changing observations")
