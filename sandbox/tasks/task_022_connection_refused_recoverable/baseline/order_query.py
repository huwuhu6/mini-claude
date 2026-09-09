import json, os, urllib.request
req=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+'/orders/42',headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']})
with urllib.request.urlopen(req,timeout=2) as response: print(json.loads(response.read()))
