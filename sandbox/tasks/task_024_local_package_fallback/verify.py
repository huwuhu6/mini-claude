import hashlib,json,os,sys,subprocess
from pathlib import Path
support_dir = Path(__file__).parent
if not (support_dir / 'verification_support.py').is_file(): support_dir = Path(__file__).parent.parent.parent / 'eval_runtime'
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_recovery_transition
root=Path(__file__).parent
if os.environ.get('EVAL_REFERENCE_CHECK')=='1':
    result=subprocess.run([sys.executable,'run_analytics.py'],cwd=root,capture_output=True,text=True)
    if result.returncode or json.loads(result.stdout).get('score')!=125.0: sys.exit('reference behavior failed')
    sys.exit(0)
t=load_trace()
require_recovery_transition(t, minimum_subjects=1)
if t.get('final_status')!='SUCCESS': sys.exit('fallback not used')
seed=os.environ.get('EVAL_HIDDEN_SEED')
if not seed: sys.exit('missing verifier-only input seed')
amount=round(100+(int(hashlib.sha256(seed.encode()).hexdigest()[:12],16)%900000)/100,2)
result=subprocess.run([sys.executable,'run_analytics.py','--amount',str(amount)],cwd=root,capture_output=True,text=True)
try: output=json.loads(result.stdout)
except json.JSONDecodeError: sys.exit('local component did not emit JSON')
if result.returncode or output.get('score')!=round(amount/10,2): sys.exit('local component did not implement dynamic lookup')
print('SUCCESS: local fallback used')
