# app.py
from db import DB_CONFIG, get_connection_string

def start_server():
    print(f"正在连接到数据库 {get_connection_string()}...")
    # 模拟业务逻辑
    if DB_CONFIG["port"] != 8888:
        print("错误：数据库端口配置不正确，服务器拒绝启动！")
        return False
    print("服务器启动成功！")
    return True

if __name__ == "__main__":
    start_server()
