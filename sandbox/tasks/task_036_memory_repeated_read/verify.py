from pathlib import Path

expected = {"service": "catalog", "region": "cn-hangzhou", "port": "8088", "retry_limit": "3"}
actual = {}
for line in Path("deployment_summary.txt").read_text(encoding="utf-8").splitlines():
    if "=" in line:
        key, value = line.split("=", 1)
        actual[key.strip()] = value.strip()
assert actual == expected, actual
assert 'SERVICE = "catalog"' in Path("release_config.py").read_text(encoding="utf-8")
