import json, os, subprocess, sys
from pathlib import Path
support_dir = Path(__file__).parent
if not (support_dir / "verification_support.py").is_file(): support_dir = Path(__file__).parent.parent.parent / "eval_runtime"
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_original_files, require_recovery_transition

root = Path(__file__).parent
ORACLE_HASHES = {
    "tests/contract.js": "593f0da337b0893a6a8bc8ba62d5acf5cd56e5c8822c0231c6696f7a95d93ec0",
    "package.json": "1f3f9646c1a5b62655d30e4d27b7af12ad307b8a105e189d41395c74c3a48b2c",
}
if os.environ.get("EVAL_REFERENCE_CHECK") == "1":
    sys.exit(subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "test"], cwd=root).returncode)
require_original_files(root, ORACLE_HASHES)
trace = load_trace()
require_recovery_transition(trace, minimum_subjects=1)
if trace.get("final_status") != "SUCCESS":
    sys.exit("Node config task did not recover")
result = subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "test"], cwd=root, capture_output=True, text=True)
if result.returncode or "PASS node config" not in result.stdout:
    sys.exit("Node config contract failed")
seed = os.environ.get("EVAL_HIDDEN_SEED")
if not seed:
    sys.exit("missing verifier-only input seed")
digest = __import__("hashlib").sha256(seed.encode()).hexdigest()
service = "svc-" + digest[:8]
port = 9000 + int(digest[8:12], 16) % 1000
retries = 1 + int(digest[12:16], 16) % 9
hidden = subprocess.run([
    "node", "-e",
    f"const fs=require('node:fs'),os=require('node:os'),path=require('node:path'); const {{loadService}}=require('./bin/launch'); const f=path.join(os.tmpdir(),'anti_loop_hidden_service.json'); fs.writeFileSync(f,JSON.stringify({{service:'{service}',port:'{port}',retries:'{retries}',ignored:true}})); const v=loadService(f); fs.unlinkSync(f); if(v.service!=='{service}'||v.port!=={port}||v.retries!=={retries}||Object.keys(v).length!==3) process.exit(1);"
], cwd=root)
if hidden.returncode:
    sys.exit("hidden Node config invariants failed")
print("SUCCESS: Node CLI configuration recovered")
