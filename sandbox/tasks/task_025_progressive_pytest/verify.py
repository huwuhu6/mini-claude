import json,os,sys,subprocess
from pathlib import Path
support_dir = Path(__file__).parent
if not (support_dir / 'verification_support.py').is_file(): support_dir = Path(__file__).parent.parent.parent / 'eval_runtime'
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_original_files, require_recovery_transition
root=Path(__file__).parent
ORACLE_HASHES={'test_discounts.py':'3581469d13c8d23be4e3da475c36275ab0c95b9eb02ba56d867d87ab0e62dab4'}
if os.environ.get('EVAL_REFERENCE_CHECK')=='1':
    sys.exit(subprocess.run([sys.executable,'-m','pytest','-q'],cwd=root).returncode)
require_original_files(root, ORACLE_HASHES)
t=load_trace()
require_recovery_transition(t, minimum_subjects=1)
if t.get('final_status')!='SUCCESS': sys.exit('tests not fixed')
if subprocess.run([sys.executable,'-m','pytest','-q'],cwd=root).returncode: sys.exit('tests still fail')
hidden=subprocess.run([sys.executable,'-c',"from discounts import *; assert subtotal([{'price':7,'quantity':4}])==28; assert apply_discount([{'price':250,'quantity':2}],'SAVE10',0.2)==540.0; assert apply_discount([{'price':19,'quantity':3}],'none',0)==57"],cwd=root)
if hidden.returncode: sys.exit('hidden discount invariants failed')
print('SUCCESS: progressive test task completed')
