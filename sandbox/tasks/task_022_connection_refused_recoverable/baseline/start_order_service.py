import json, os, urllib.request
req=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+'/start',method='POST',headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']},data=b'{}')
with urllib.request.urlopen(req,timeout=2) as response: print(json.loads(response.read()))
