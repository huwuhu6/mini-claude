import os
import subprocess
import sys

env = {**os.environ, "PYTHONUTF8": "1"}

result = subprocess.run(
    ["python", "run_test.py"],
    capture_output=True,
    env=env,
    timeout=30,
)
output = result.stdout.decode("utf-8", errors="replace")
output += result.stderr.decode("utf-8", errors="replace")

if "【🎉 终极大通关：所有跨模块测试完美通过！】" in output:
    sys.exit(0)
else:
    sys.exit(1)
