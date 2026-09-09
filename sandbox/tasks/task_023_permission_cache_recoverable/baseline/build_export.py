import hashlib,json,pathlib
cfg={k:v for k,v in (line.split('=',1) for line in pathlib.Path('cache_config.ini').read_text().splitlines())}
if cfg['output_dir'].startswith('protected'): raise PermissionError('EACCES: evaluator fixture rejected protected output')
records=[{'id':1,'amount':1250},{'id':2,'amount':900}]
payload={'format':cfg['format'],'records':records,'sha256':hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()}
out=pathlib.Path(cfg['output_dir'])
out.mkdir(parents=True,exist_ok=True)
(out/'orders.json').write_text(json.dumps(payload,sort_keys=True),encoding='utf-8')
print(payload)
