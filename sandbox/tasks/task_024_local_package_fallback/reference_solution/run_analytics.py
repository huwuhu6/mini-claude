import importlib,json
mod=importlib.import_module('vendor.metrics_core')
print(json.dumps({'score':mod.score({'amount':1250}),'provider':'metrics-core'}))
