import json,os,sys,urllib.request
from pathlib import Path
support_dir = Path(__file__).parent
if not (support_dir / 'verification_support.py').is_file(): support_dir = Path(__file__).parent.parent.parent / 'eval_runtime'
sys.path.insert(0, str(support_dir))
from verification_support import _controller_audit, load_trace, require_recovery_transition
root=Path(__file__).parent


def _require_grounded_business_recovery(trace):
    """Verify the recovery transition against evaluator-owned observations."""
    audit = _controller_audit('task022 business recovery')
    observations = [
        item for item in audit.get('observations', [])
        if isinstance(item, dict) and item.get('role') == 'agent'
    ]

    def payload_for(item):
        return item.get('payload') if isinstance(item.get('payload'), dict) else {}

    initial_health = next(
        (index for index, item in enumerate(observations)
         if item.get('endpoint') == '/health' and item.get('status_code') == 503),
        None,
    )
    start = next(
        (index for index, item in enumerate(observations)
         if item.get('endpoint') == '/start' and item.get('status_code') == 200),
        None,
    )
    recovered_health = next(
        (index for index, item in enumerate(observations)
         if item.get('endpoint') == '/health' and item.get('status_code') == 200
         and payload_for(item).get('status') == 'READY'),
        None,
    )
    recovered_order = next(
        (index for index, item in enumerate(observations)
         if item.get('endpoint') == '/orders/42' and item.get('status_code') == 200
         and payload_for(item) == {'order_id': 42, 'state': 'PAID', 'total': 1250}),
        None,
    )
    if initial_health is None or start is None or recovered_health is None:
        raise SystemExit('evaluator evidence missing health recovery transition')
    if not (initial_health < start < recovered_health):
        raise SystemExit('health recovery evidence is not ordered around initialization')
    if recovered_order is None or recovered_order <= start:
        raise SystemExit('evaluator evidence missing final business outcome')


def get(path):
    req=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+path,headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']})
    with urllib.request.urlopen(req,timeout=2) as r: return json.loads(r.read())
if os.environ.get('EVAL_REFERENCE_CHECK')=='1':
    sys.exit(0 if get('/health').get('status')=='READY' and get('/orders/42').get('state')=='PAID' else 1)
t=load_trace()
require_recovery_transition(t, minimum_subjects=1)
if t.get('final_status')!='SUCCESS': sys.exit('service not recovered')
_require_grounded_business_recovery(t)
print('SUCCESS: service recovered')
