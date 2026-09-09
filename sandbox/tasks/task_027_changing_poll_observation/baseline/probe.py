import json,os,urllib.request
req=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+'/probe',headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']})
with urllib.request.urlopen(req,timeout=2) as r: print(json.loads(r.read()))
