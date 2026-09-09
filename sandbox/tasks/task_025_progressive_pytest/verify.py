import json,os,sys,subprocess
from pathlib import Path
root=Path(__file__).parent
if os.environ.get('EVAL_REFERENCE_CHECK')=='1':
    sys.exit(subprocess.run([sys.executable,'-m','pytest','-q'],cwd=root).returncode)
t=json.loads(Path(os.environ['EVAL_TRACE_PATH']).read_text())
if t.get('final_status')!='SUCCESS': sys.exit('tests not fixed')
if subprocess.run([sys.executable,'-m','pytest','-q'],cwd=root).returncode: sys.exit('tests still fail')
print('SUCCESS: progressive test task completed')
