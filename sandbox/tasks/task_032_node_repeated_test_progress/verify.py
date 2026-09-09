import json, os, subprocess, sys
from pathlib import Path

root = Path(__file__).parent
if os.environ.get("EVAL_REFERENCE_CHECK") == "1":
    sys.exit(subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "test"], cwd=root).returncode)
trace = json.loads(Path(os.environ["EVAL_TRACE_PATH"]).read_text(encoding="utf-8"))
if trace.get("final_status") != "SUCCESS":
    sys.exit("agent did not report success")
result = subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "test"], cwd=root, capture_output=True, text=True)
if result.returncode or "PASS node invoice" not in result.stdout:
    sys.exit("npm test business verification failed")
if not (root / "tests" / "run_tests.js").is_file():
    sys.exit("test topology removed")
print("SUCCESS: local npm test passed")
