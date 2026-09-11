import json, os, subprocess, sys
from pathlib import Path
support_dir = Path(__file__).parent
if not (support_dir / "verification_support.py").is_file(): support_dir = Path(__file__).parent.parent.parent / "eval_runtime"
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_original_files, require_recovery_transition

root = Path(__file__).parent
ORACLE_HASHES = {
    "tests/run_tests.js": "a4fb379c9f07cacb3bf00e3a23fac68035f18ed8fa5935e886155d31271b9adb",
    "package.json": "1f701874e9f35007e395f1fc5e6e567ba065d2d604a988c41dc13aee891f4592",
}
if os.environ.get("EVAL_REFERENCE_CHECK") == "1":
    sys.exit(subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "test"], cwd=root).returncode)
require_original_files(root, ORACLE_HASHES)
trace = load_trace()
require_recovery_transition(trace, minimum_subjects=1)
if trace.get("final_status") != "SUCCESS":
    sys.exit("agent did not report success")
result = subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "test"], cwd=root, capture_output=True, text=True)
if result.returncode or "PASS node invoice" not in result.stdout:
    sys.exit("npm test business verification failed")
if not (root / "tests" / "run_tests.js").is_file():
    sys.exit("test topology removed")
seed = os.environ.get("EVAL_HIDDEN_SEED")
if not seed:
    sys.exit("missing verifier-only input seed")
digest = __import__("hashlib").sha256(seed.encode()).digest()
cases = [
    [{"price": 3 + digest[0] % 17, "quantity": 1 + digest[1] % 5},
     {"price": 5 + digest[2] % 23, "quantity": 1 + digest[3] % 4}],
    [{"price": 7 + digest[4] % 19, "quantity": 1 + digest[5] % 4},
     {"price": 11 + digest[6] % 29, "quantity": 1 + digest[7] % 3}],
]
expected = [sum(item["price"] * item["quantity"] for item in items) for items in cases]
hidden = subprocess.run([
    "node", "-e",
    f"const {{total,payable}}=require('./src/discount'); const cases={json.dumps(cases)}; const expected={json.dumps(expected)}; for(let i=0;i<cases.length;i++){{if(total(cases[i])!==expected[i]||payable(cases[i],'VIP')!==expected[i]*0.9||payable(cases[i],'NONE')!==expected[i]) process.exit(1);}}"
], cwd=root)
if hidden.returncode:
    sys.exit("hidden Node discount invariants failed")
print("SUCCESS: local npm test passed")
