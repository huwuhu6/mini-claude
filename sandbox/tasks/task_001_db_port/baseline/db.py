# db.py
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "admin"
}

def get_connection_string():
    # 故意留一个过时的内部逻辑
    return f"postgresql://{DB_CONFIG['user']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/main_db"
