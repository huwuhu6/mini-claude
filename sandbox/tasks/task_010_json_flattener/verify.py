import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

if "json_flattener" in sys.modules:
    del sys.modules["json_flattener"]
from json_flattener import flatten_json

test_cases = [
    ({"a": 1}, {"a": 1}),
    ({"a": {"b": 1}}, {"a.b": 1}),
    ({"a": {"b": 1, "c": {"d": 2}}, "e": 3}, {"a.b": 1, "a.c.d": 2, "e": 3}),
    ({"x": {"y": {"z": {"w": 42}}}}, {"x.y.z.w": 42}),
    ({}, {}),
    ({"list_key": [1, 2, 3]}, {"list_key": [1, 2, 3]}),
    ({"a": {"b": None, "c": True, "d": "hello"}}, {"a.b": None, "a.c": True, "a.d": "hello"}),
]

for inp, expected in test_cases:
    result = flatten_json(inp)
    if result != expected:
        print(f"[FAIL] flatten_json({inp}) = {result}, expected {expected}")
        sys.exit(1)

print("[PASS] 所有测试用例通过")
sys.exit(0)
