#!/bin/sh
set -e

# 设置环境变量
export FLASK_APP=run.py
export DATABASE_URL="sqlite:////data/app.db"
export PREFERRED_URL_SCHEME="http"

echo "=== 容器启动 ==="

# 检查数据库目录
echo "创建必要目录..."
mkdir -p /app/app/backups /app/app/static/uploads/avatars /app/app/static/uploads/logos \
         /app/app/static/uploads/favicons /app/app/static/uploads/backgrounds \
         /data/backups /data/uploads/avatars /data/uploads/logos \
         /data/uploads/favicons /data/uploads/backgrounds
chmod -R 777 /app/app/backups /app/app/static/uploads /data

# 检查宿主机数据库文件
if [ ! -f /data/app.db ]; then
    echo "宿主机数据库不存在，创建新数据库..."
    
    # 直接在/data目录中创建数据库
    touch /data/app.db
    chmod 666 /data/app.db
    
    # 直接使用python脚本创建数据库结构，但不创建用户
    # 这样Flask的before_first_request将负责创建管理员用户
    cd /app
    python3 << EOF
from app import create_app, db
app = create_app()
with app.app_context():
    db.create_all()
    print("数据库表结构创建完成")
EOF
    
    echo "数据库初始化完成，管理员用户将由应用程序创建"
else
    echo "使用现有数据库..."
    chmod 666 /data/app.db
    
    # 确保数据库结构是最新的（db.create_all() 只会创建不存在的表，不会修改已存在的表）
    cd /app
    python3 << EOF
from app import create_app, db
app = create_app()
with app.app_context():
    db.create_all()
    print("数据库结构检查完成")
EOF
fi

# 创建从/app/app.db到/data/app.db的符号链接
if [ -f /app/app.db ]; then
    rm /app/app.db
fi
ln -sf /data/app.db /app/app.db
echo "数据库符号链接已创建"

# 检查Nginx配置文件
if [ ! -f /etc/nginx/http.d/default.conf ]; then
    echo "Nginx配置文件不存在，复制默认配置..."
    mkdir -p /etc/nginx/http.d/
    cp /defaults/nginx.conf /etc/nginx/http.d/default.conf
    echo "Nginx配置文件已复制"
fi

# 进行数据库备份（容器启动时）
if [ -f /data/app.db ] && [ -s /data/app.db ]; then
    BACKUP_FILE="/app/app/backups/startup_backup_$(date +%Y%m%d%H%M%S).db3"
    echo "创建启动时数据库备份: $BACKUP_FILE"
    cp /data/app.db "$BACKUP_FILE" || echo "备份失败，继续启动..."
fi

echo "=== 启动应用服务 ==="
exec supervisord -c /etc/supervisor/conf.d/supervisord.conf 