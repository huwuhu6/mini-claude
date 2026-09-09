import importlib,json,pathlib
cfg={k:v for k,v in (line.split('=',1) for line in pathlib.Path('resolver.ini').read_text().splitlines())}
if cfg['provider']!='metrics-core': raise LookupError('No matching distribution found for analytics-adapter==3.x')
mod=importlib.import_module('vendor.metrics_core')
event={'amount':1250}
print(json.dumps({'score':mod.score(event),'provider':cfg['provider']}))
