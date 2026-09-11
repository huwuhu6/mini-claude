import os,sys,urllib.error,urllib.request
request=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+'/health',headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']})
try: urllib.request.urlopen(request,timeout=2)
except urllib.error.HTTPError as error: print(error.read().decode()); sys.exit(1)
