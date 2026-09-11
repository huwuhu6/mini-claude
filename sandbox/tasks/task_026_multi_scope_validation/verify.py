import json,os,sys,subprocess
from pathlib import Path
support_dir = Path(__file__).parent
if not (support_dir / 'verification_support.py').is_file(): support_dir = Path(__file__).parent.parent.parent / 'eval_runtime'
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_original_files, require_recovery_transition, require_distinct_subjects
root=Path(__file__).parent
ORACLE_HASHES={'test_auth.py':'63424c9fd64cb9e7031f1d983353fcb3efc5c6be7213176901da384a8dc1155a','test_billing.py':'acf7095db153f79d18977a0f9efa792e465c024c68516547c1cf17abc895182a','test_report.py':'8f18f44ddd6f22d1a78936d442091360ae0972c4253386bf264642aa3c3225cb'}
if os.environ.get('EVAL_REFERENCE_CHECK')=='1': sys.exit(subprocess.run([sys.executable,'-m','pytest','-q'],cwd=root).returncode)
require_original_files(root, ORACLE_HASHES)
t=load_trace()
require_recovery_transition(t, minimum_subjects=1)
require_distinct_subjects(t, ('test_auth.py','test_billing.py','test_report.py'), minimum=3)
if t.get('final_status')!='SUCCESS' or subprocess.run([sys.executable,'-m','pytest','-q'],cwd=root).returncode: sys.exit('scopes incomplete')
hidden=subprocess.run([sys.executable,'-c',"from auth import can_view; from billing import net_amount; from report import label; assert can_view({'role':'admin'}); assert not can_view({'role':'user'}); assert net_amount(100,15)==85; assert label(0)=='empty'; assert label(1)=='paid'"],cwd=root)
if hidden.returncode: sys.exit('hidden scope invariants failed')
print('SUCCESS: multi-scope validation completed')
