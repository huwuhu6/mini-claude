import json,os,sys,urllib.request
from pathlib import Path
root=Path(__file__).parent
def get(path):
    req=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+path,headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']})
    with urllib.request.urlopen(req,timeout=2) as r: return json.loads(r.read())
if os.environ.get('EVAL_REFERENCE_CHECK')=='1':
    sys.exit(0 if get('/health').get('status')=='READY' and get('/orders/42').get('state')=='PAID' else 1)
t=json.loads(Path(os.environ['EVAL_TRACE_PATH']).read_text())
if t.get('final_status')!='SUCCESS': sys.exit('service not recovered')
if get('/health').get('status')!='READY' or get('/orders/42') != {'order_id':42,'state':'PAID','total':1250}: sys.exit('business outcome not READY')
print('SUCCESS: service recovered')
