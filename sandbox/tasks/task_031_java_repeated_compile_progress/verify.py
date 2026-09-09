import json, os, subprocess, sys
from pathlib import Path

root = Path(__file__).parent
if os.environ.get("EVAL_REFERENCE_CHECK") == "1":
    sys.exit(subprocess.run(["cmd", "/c", "run_tests.cmd"], cwd=root).returncode)
trace = json.loads(Path(os.environ["EVAL_TRACE_PATH"]).read_text(encoding="utf-8"))
if trace.get("final_status") != "SUCCESS":
    sys.exit("agent did not report success")
result = subprocess.run(["cmd", "/c", "run_tests.cmd"], cwd=root, capture_output=True, text=True)
if result.returncode or "PASS java invoice" not in result.stdout:
    sys.exit("javac/java business verification failed")
if not (root / "src" / "main" / "java" / "com" / "example" / "Invoice.java").is_file():
    sys.exit("source removed")
print("SUCCESS: javac compile and Java execution passed")
