import json, os, subprocess, sys, tempfile
from pathlib import Path
support_dir = Path(__file__).parent
if not (support_dir / "verification_support.py").is_file(): support_dir = Path(__file__).parent.parent.parent / "eval_runtime"
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_original_files, require_recovery_transition

root = Path(__file__).parent
ORACLE_HASHES = {
    "src/test/java/com/example/InvoiceTest.java": "3d74c3a6ebc10e97e40f259c2762c090563235efaa6a04191ef1eca9546ed911",
    "run_tests.cmd": "d62c44530410b4dafd474275268bd5e76771edaf4bda8959c5b7d36855931211",
}
if os.environ.get("EVAL_REFERENCE_CHECK") == "1":
    sys.exit(subprocess.run(["cmd", "/c", "run_tests.cmd"], cwd=root).returncode)
require_original_files(root, ORACLE_HASHES)
trace = load_trace()
require_recovery_transition(trace, minimum_subjects=1)
if trace.get("final_status") != "SUCCESS":
    sys.exit("agent did not report success")
result = subprocess.run(["cmd", "/c", "run_tests.cmd"], cwd=root, capture_output=True, text=True)
if result.returncode or "PASS java invoice" not in result.stdout:
    sys.exit("javac/java business verification failed")
if not (root / "src" / "main" / "java" / "com" / "example" / "Invoice.java").is_file():
    sys.exit("source removed")
hidden_source = """
import com.example.Invoice;
public final class HiddenInvoiceCheck {
    public static void main(String[] args) {
        if (Invoice.payableCents(10000, 10) != 8975) throw new AssertionError();
        if (Invoice.payableCents(731, 0) != 706) throw new AssertionError();
        boolean rejected = false;
        try { Invoice.payableCents(10, 101); } catch (IllegalArgumentException expected) { rejected = true; }
        if (!rejected) throw new AssertionError();
    }
}
"""
with tempfile.TemporaryDirectory() as temp_dir:
    source = Path(temp_dir) / "HiddenInvoiceCheck.java"
    source.write_text(hidden_source, encoding="utf-8")
    hidden_compile = subprocess.run(["javac", "-cp", str(root / "out"), "-d", temp_dir, str(source)], cwd=root)
    hidden_run = subprocess.run(["java", "-cp", os.pathsep.join((temp_dir, str(root / "out"))), "HiddenInvoiceCheck"], cwd=root)
    if hidden_compile.returncode or hidden_run.returncode:
        sys.exit("hidden Java invariants failed")
print("SUCCESS: javac compile and Java execution passed")
