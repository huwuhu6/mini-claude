import argparse,importlib,json,pathlib
parser=argparse.ArgumentParser()
parser.add_argument('--amount', type=float, default=1250)
args=parser.parse_args()
cfg={k:v for k,v in (line.split('=',1) for line in pathlib.Path('resolver.ini').read_text().splitlines())}
if cfg['provider']!='metrics-core': raise LookupError('No matching distribution found for analytics-adapter==3.x')
mod=importlib.import_module('vendor.metrics_core')
event={'amount':args.amount}
print(json.dumps({'score':mod.score(event),'provider':cfg['provider']}))
