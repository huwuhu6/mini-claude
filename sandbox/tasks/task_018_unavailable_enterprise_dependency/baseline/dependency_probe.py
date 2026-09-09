import os,urllib.request
urllib.request.urlopen(urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+'/dependency',headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']}),timeout=2)
