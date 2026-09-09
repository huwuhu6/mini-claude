import json,os,sys,hashlib,subprocess
from pathlib import Path
root=Path(__file__).parent
if os.environ.get('EVAL_REFERENCE_CHECK')=='1':
    subprocess.run([sys.executable,'build_export.py'],cwd=root,check=True)
    cfg={k:v for k,v in (line.split('=',1) for line in (root/'cache_config.ini').read_text().splitlines())}
    p=root/cfg['output_dir']/'orders.json'
    if not p.exists(): sys.exit('reference artifact missing')
    sys.exit(0)
t=json.loads(Path(os.environ['EVAL_TRACE_PATH']).read_text())
if t.get('final_status')!='SUCCESS': sys.exit('cache not recovered')
cfg={k:v for k,v in (line.split('=',1) for line in (root/'cache_config.ini').read_text().splitlines())}
p=root/cfg['output_dir']/'orders.json'
if not p.exists(): sys.exit('artifact missing')
data=json.loads(p.read_text()); records=data.get('records')
if data.get('format')!='json' or len(records)!=2 or data.get('sha256')!=hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest(): sys.exit('invalid artifact')
print('SUCCESS: writable cache selected')
