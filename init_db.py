#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""手动初始化数据库脚本"""

import os
import sys
from app import create_app, db
from app.models import User, Category, Website, SiteSettings, InvitationCode, Tag, Background, OperationLog, DeadlinkCheck

# 获取数据库路径
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'app.db')

print("=" * 60)
print("数据库初始化脚本")
print("=" * 60)
print(f"数据库路径: {db_path}")

# 检查数据库是否存在
db_exists = os.path.exists(db_path)
if db_exists:
    print(f"\n数据库文件已存在: {db_path}")
    print("\n请选择操作:")
    print("  1. 删除并重新创建数据库（会丢失所有数据）")
    print("  2. 更新数据库结构（添加缺失的字段，保留数据）")
    print("  3. 取消操作")
    response = input("\n请选择 (1/2/3): ").strip()
    
    if response == '1':
        try:
            os.remove(db_path)
            print(f"✓ 已删除现有数据库文件")
            db_exists = False
        except Exception as e:
            print(f"✗ 删除数据库文件失败: {e}")
            sys.exit(1)
    elif response == '2':
        print("\n将更新数据库结构，添加缺失的字段...")
        # 继续执行，后面会处理字段更新
    else:
        print("取消操作")
        sys.exit(0)

# 创建应用并初始化数据库
try:
    print("\n正在创建应用...")
    app = create_app()
    print("✓ 应用创建成功")
    
    print("\n正在初始化数据库...")
    with app.app_context():
        # 创建所有表
        db.create_all()
        print("✓ 数据库表结构创建完成")
        
        # 如果是已有数据库，检查并添加缺失的字段
        if db_exists:
            print("\n正在检查并更新数据库字段...")
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 检查 site_settings 表的字段
            cursor.execute("PRAGMA table_info(site_settings)")
            columns = cursor.fetchall()
            column_names = [column[1] for column in columns]
            
            # AI 搜索配置字段
            ai_fields = [
                ('ai_search_enabled', 'BOOLEAN DEFAULT 0'),
                ('ai_api_base_url', 'VARCHAR(512)'),
                ('ai_api_key', 'VARCHAR(512)'),
                ('ai_model_name', 'VARCHAR(128)'),
                ('ai_temperature', 'REAL DEFAULT 0.7'),
                ('ai_max_tokens', 'INTEGER DEFAULT 500')
            ]
            
            added_count = 0
            for field_name, field_def in ai_fields:
                if field_name not in column_names:
                    try:
                        sql = f"ALTER TABLE site_settings ADD COLUMN {field_name} {field_def}"
                        cursor.execute(sql)
                        print(f"  ✓ 已添加字段: {field_name}")
                        added_count += 1
                    except sqlite3.Error as e:
                        print(f"  ✗ 添加字段 {field_name} 失败: {e}")
                else:
                    print(f"  ✓ 字段已存在: {field_name}")
            
            if added_count > 0:
                conn.commit()
                print(f"\n✓ 已添加 {added_count} 个新字段")
            else:
                print("\n✓ 所有字段都已存在，无需更新")
            
            conn.close()
        
        # 验证表是否创建成功
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        
        print(f"\n数据库中的表 ({len(tables)} 个):")
        for table in sorted(tables):
            print(f"  - {table}")
        
        # 创建默认管理员账户（与 app/__init__.py 中的逻辑保持一致）
        print("\n正在创建默认管理员账户...")
        admin = User.query.filter_by(username=app.config['ADMIN_USERNAME']).first()
        admin_by_email = User.query.filter_by(email=app.config['ADMIN_EMAIL']).first()
        if not admin and not admin_by_email:
            admin = User(
                username=app.config['ADMIN_USERNAME'],
                email=app.config['ADMIN_EMAIL'],
                is_admin=True,
                is_superadmin=True
            )
            admin.set_password(app.config['ADMIN_PASSWORD'])
            db.session.add(admin)
            db.session.commit()
            print(f"✓ 默认管理员账户创建成功")
            print(f"  用户名: {app.config['ADMIN_USERNAME']}")
            print(f"  邮箱: {app.config['ADMIN_EMAIL']}")
            print(f"  密码: {app.config['ADMIN_PASSWORD']}")
        elif admin_by_email and (not admin or admin.username != app.config['ADMIN_USERNAME']):
            print(f"  ! 已存在邮箱为 {app.config['ADMIN_EMAIL']} 的用户，跳过创建默认管理员")
        elif admin and not admin.is_superadmin:
            admin.is_superadmin = True
            db.session.commit()
            print("  ✓ 已将现有管理员升级为超级管理员")
        else:
            print("  ! 管理员账户已存在，跳过创建")
        
        # 初始化站点设置
        print("\n正在初始化站点设置...")
        settings = SiteSettings.get_settings()
        print("✓ 站点设置初始化完成")
        
    print("\n" + "=" * 60)
    print("数据库初始化完成！")
    print("=" * 60)
    print(f"\n数据库文件位置: {db_path}")
    print(f"文件大小: {os.path.getsize(db_path) / 1024:.2f} KB")
    
except Exception as e:
    print(f"\n✗ 初始化失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

