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
import sqlite3
import os

app = create_app()
with app.app_context():
    db.create_all()
    print("数据库结构检查完成")
    
    # 检查并添加缺失的字段
    db_path = "/data/app.db"
    if os.path.exists(db_path):
        print("正在检查并更新数据库字段...")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查 site_settings 表的字段
        cursor.execute("PRAGMA table_info(site_settings)")
        columns = cursor.fetchall()
        column_names = [column[1] for column in columns]
        
        # AI 搜索配置字段（新添加的字段）
        ai_fields = [
            ('ai_search_enabled', 'BOOLEAN DEFAULT 0'),
            ('ai_search_allow_anonymous', 'BOOLEAN DEFAULT 0'),
            ('ai_api_base_url', 'VARCHAR(512)'),
            ('ai_api_key', 'VARCHAR(512)'),
            ('ai_model_name', 'VARCHAR(128)'),
            ('ai_temperature', 'REAL DEFAULT 0.7'),
            ('ai_max_tokens', 'INTEGER DEFAULT 500')
        ]
        
        # 向量搜索配置字段（新添加的字段）
        vector_fields = [
            ('vector_search_enabled', 'BOOLEAN DEFAULT 0'),
            ('qdrant_url', 'VARCHAR(512) DEFAULT \'http://localhost:6333\''),
            ('embedding_model', 'VARCHAR(128) DEFAULT \'text-embedding-3-small\''),
            ('vector_similarity_threshold', 'REAL DEFAULT 0.3'),
            ('vector_max_results', 'INTEGER DEFAULT 50')
        ]
        
        # 过渡页设置字段（如果缺失也需要添加）
        transition_fields = [
            ('enable_transition', 'BOOLEAN DEFAULT 0'),
            ('transition_time', 'INTEGER DEFAULT 5'),
            ('admin_transition_time', 'INTEGER DEFAULT 3'),
            ('transition_ad1', 'TEXT'),
            ('transition_ad2', 'TEXT'),
            ('transition_remember_choice', 'BOOLEAN DEFAULT 1'),
            ('transition_show_description', 'BOOLEAN DEFAULT 1'),
            ('transition_theme', 'VARCHAR(32) DEFAULT \'default\''),
            ('transition_color', 'VARCHAR(32) DEFAULT \'#6e8efb\'')
        ]
        
        # 公告设置字段（如果缺失也需要添加）
        announcement_fields = [
            ('announcement_enabled', 'BOOLEAN DEFAULT 0'),
            ('announcement_title', 'VARCHAR(128)'),
            ('announcement_content', 'TEXT'),
            ('announcement_start', 'DATETIME'),
            ('announcement_end', 'DATETIME'),
            ('announcement_remember_days', 'INTEGER DEFAULT 7')
        ]
        
        # PC/移动端背景字段（如果缺失也需要添加）
        background_fields = [
            ('pc_background_type', 'VARCHAR(32) DEFAULT \'none\''),
            ('pc_background_url', 'VARCHAR(512)'),
            ('mobile_background_type', 'VARCHAR(32) DEFAULT \'none\''),
            ('mobile_background_url', 'VARCHAR(512)')
        ]
        
        # 合并所有需要检查的字段
        all_fields = ai_fields + vector_fields + transition_fields + announcement_fields + background_fields
        
        added_count = 0
        for field_name, field_def in all_fields:
            if field_name not in column_names:
                try:
                    sql = f"ALTER TABLE site_settings ADD COLUMN {field_name} {field_def}"
                    cursor.execute(sql)
                    print(f"  ✓ 已添加字段: {field_name}")
                    added_count += 1
                except sqlite3.Error as e:
                    print(f"  ✗ 添加字段 {field_name} 失败: {e}")
        
        if added_count > 0:
            conn.commit()
            print(f"✓ 已添加 {added_count} 个新字段")
        else:
            print("✓ 所有字段都已存在，无需更新")
        
        conn.close()
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