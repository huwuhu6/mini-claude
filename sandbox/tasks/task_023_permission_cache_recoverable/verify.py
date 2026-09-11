import json,os,sys,hashlib,subprocess
from pathlib import Path
support_dir = Path(__file__).parent
if not (support_dir / 'verification_support.py').is_file(): support_dir = Path(__file__).parent.parent.parent / 'eval_runtime'
sys.path.insert(0, str(support_dir))
from verification_support import load_trace, require_original_files, require_recovery_transition
root=Path(__file__).parent
if os.environ.get('EVAL_REFERENCE_CHECK')=='1':
    subprocess.run([sys.executable,'build_export.py'],cwd=root,check=True)
    cfg={k:v for k,v in (line.split('=',1) for line in (root/'cache_config.ini').read_text().splitlines())}
    p=root/cfg['output_dir']/'orders.json'
    if not p.exists(): sys.exit('reference artifact missing')
    sys.exit(0)
t=load_trace()
require_recovery_transition(t, minimum_subjects=1)
if t.get('final_status')!='SUCCESS': sys.exit('cache not recovered')
require_original_files(root, {
    'build_export.py': '09538fa070d081234128960630d3b5d581fa03e01148d1d55c277efad1f88858'
})
cfg={k:v for k,v in (line.split('=',1) for line in (root/'cache_config.ini').read_text().splitlines())}
output_dir=Path(cfg['output_dir'])
if output_dir.is_absolute() or '..' in output_dir.parts: sys.exit('output path escapes workspace')
# Re-run the evaluator-owned generator after the Agent has exited.  The hash
# gate above makes a hand-written artifact or patched generator non-causal.
generated=subprocess.run([sys.executable, 'build_export.py'], cwd=root, capture_output=True, text=True)
if generated.returncode: sys.exit('generator did not complete in recovered location')
p=root/output_dir/'orders.json'
if not p.exists(): sys.exit('artifact missing')
try: data=json.loads(p.read_text(encoding='utf-8')); records=data.get('records')
except (OSError, json.JSONDecodeError): sys.exit('invalid artifact')
expected=[{'id':1,'amount':1250},{'id':2,'amount':900}]
if data.get('format')!='json' or records!=expected or data.get('sha256')!=hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest(): sys.exit('invalid artifact')
print('SUCCESS: writable cache selected')
