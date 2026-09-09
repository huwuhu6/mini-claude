import json,os,urllib.request
req=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+'/export',method='POST',headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']},data=b'{}')
with urllib.request.urlopen(req,timeout=2) as r: data=json.loads(r.read())
open('orders_export.json','w',encoding='utf-8').write(json.dumps(data,sort_keys=True))
print(data)
