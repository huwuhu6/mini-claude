import argparse,importlib,json
parser=argparse.ArgumentParser()
parser.add_argument('--amount', type=float, default=1250)
args=parser.parse_args()
mod=importlib.import_module('vendor.metrics_core')
print(json.dumps({'score':mod.score({'amount':args.amount}),'provider':'metrics-core'}))
