import json, os, urllib.request
def request(path):
    req=urllib.request.Request(os.environ['EVAL_FIXTURE_URL']+path,headers={'X-Fixture-Token':os.environ['EVAL_FIXTURE_TOKEN']})
    with urllib.request.urlopen(req,timeout=2) as response: return json.loads(response.read())
if __name__=='__main__': print(request('/health'))
