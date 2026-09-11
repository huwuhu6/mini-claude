from pathlib import Path
import os
import urllib.request

req = urllib.request.Request(
    os.environ['EVAL_FIXTURE_URL'] + '/start',
    method='POST',
    headers={'X-Fixture-Token': os.environ['EVAL_FIXTURE_TOKEN']},
    data=b'{}',
)
with urllib.request.urlopen(req, timeout=2):
    pass
Path('reference_used.txt').write_text('service client retained', encoding='utf-8')
