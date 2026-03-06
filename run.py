from app import create_app, db
from app.models import User, Category, Website, InvitationCode
import urllib3

# 禁用不安全请求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 创建应用实例，设置SQLite支持多线程
app = create_app()

# 配置SQLite为支持多线程模式
from sqlalchemy import event
from sqlalchemy.engine import Engine
import sqlite3

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")  # 写入前日志模式，提高并发能力
        cursor.execute("PRAGMA synchronous=NORMAL;")  # 在确保安全的同时提高性能
        cursor.execute("PRAGMA foreign_keys=ON;")  # 强制外键约束
        cursor.close()

@app.shell_context_processor
def make_shell_context():
    return {
        'db': db, 
        'User': User, 
        'Category': Category, 
        'Website': Website, 
        'InvitationCode': InvitationCode
    }

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0') 