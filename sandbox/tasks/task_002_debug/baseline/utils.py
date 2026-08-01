# utils.py
import json

def format_user_data(name, age):
    result = {
        "user_name": name,
        "user_age": int(age),
        "status": "active"
    }
    return json.dumps(result)

def verify_system_env():
    # 模拟环境检查：检查必要的环境配置
    import os
    secret = os.environ.get("API_SECRET", None)
    if secret is None:
        print("[WARNING] API_SECRET not set, using default secret for development.")
    return True
