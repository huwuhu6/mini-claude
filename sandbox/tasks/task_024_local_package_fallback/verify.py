import json,os,sys,subprocess
from pathlib import Path
root=Path(__file__).parent
if os.environ.get('EVAL_REFERENCE_CHECK')=='1':
    result=subprocess.run([sys.executable,'run_analytics.py'],cwd=root,capture_output=True,text=True)
    if result.returncode or json.loads(result.stdout).get('score')!=125.0: sys.exit('reference behavior failed')
    sys.exit(0)
t=json.loads(Path(os.environ['EVAL_TRACE_PATH']).read_text())
if t.get('final_status')!='SUCCESS': sys.exit('fallback not used')
result=subprocess.run([sys.executable,'run_analytics.py'],cwd=root,capture_output=True,text=True)
if result.returncode or json.loads(result.stdout).get('score')!=125.0 or json.loads(result.stdout).get('provider')!='metrics-core': sys.exit('local component did not run')
print('SUCCESS: local fallback used')
