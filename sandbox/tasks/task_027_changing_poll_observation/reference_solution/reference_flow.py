import json,os,urllib.request
h={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']}
for _ in range(4):
    with urllib.request.urlopen(urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+'/probe',headers=h)): pass
r=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+'/export',method='POST',headers=h,data=b'{}')
with urllib.request.urlopen(r) as response: data=json.loads(response.read())
open('orders_export.json','w').write(json.dumps(data))
