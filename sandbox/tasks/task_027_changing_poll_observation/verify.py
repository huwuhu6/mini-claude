import json,os,sys,urllib.request,hashlib
from pathlib import Path
root=Path(__file__).parent
if os.environ.get('EVAL_REFERENCE_CHECK')=='1':
    data=json.loads((root/'orders_export.json').read_text())
    if data.get('artifact')!='orders-v1' or data.get('rows')!=2: sys.exit('bad reference export')
    sys.exit(0)
t=json.loads(Path(os.environ['EVAL_TRACE_PATH']).read_text())
if t.get('final_status')!='SUCCESS': sys.exit('polling incomplete')
req=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+'/state',headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']})
with urllib.request.urlopen(req,timeout=2) as r: state=json.loads(r.read())
if state.get('probe_count',0)<4 or not state.get('exported'): sys.exit('state machine did not reach export')
data=json.loads((root/'orders_export.json').read_text()) if (root/'orders_export.json').exists() else {}
if data.get('artifact')!='orders-v1' or data.get('rows')!=2: sys.exit('invalid export')
if data.get('sha256')!=hashlib.sha256(b'orders-v1:2').hexdigest(): sys.exit('invalid export hash')
print('SUCCESS: changing observation tolerated')
