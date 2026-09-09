import hashlib,json,pathlib
records=[{'id':1,'amount':1250},{'id':2,'amount':900}]
payload={'format':'json','records':records,'sha256':hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()}
out=pathlib.Path('runtime/output')
out.mkdir(parents=True,exist_ok=True)
(out/'orders.json').write_text(json.dumps(payload,sort_keys=True),encoding='utf-8')
