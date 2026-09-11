import json,os,sys,urllib.request
from pathlib import Path
support_dir = Path(__file__).parent
if not (support_dir / 'verification_support.py').is_file(): support_dir = Path(__file__).parent.parent.parent / 'eval_runtime'
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_recovery_transition
root=Path(__file__).parent
def get(path):
    req=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+path,headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']})
    with urllib.request.urlopen(req,timeout=2) as r: return json.loads(r.read())
if os.environ.get('EVAL_REFERENCE_CHECK')=='1':
    sys.exit(0 if get('/health').get('status')=='READY' and get('/orders/42').get('state')=='PAID' else 1)
t=load_trace()
require_recovery_transition(t, minimum_subjects=1)
if t.get('final_status')!='SUCCESS': sys.exit('service not recovered')
if get('/health').get('status')!='READY' or get('/orders/42') != {'order_id':42,'state':'PAID','total':1250}: sys.exit('business outcome not READY')
print('SUCCESS: service recovered')
