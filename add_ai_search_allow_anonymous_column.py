#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
添加 ai_search_allow_anonymous 字段到数据库
"""

import sqlite3
import os

def add_column():
    """添加 ai_search_allow_anonymous 字段"""
    # 从配置文件获取数据库路径
    try:
        from config import Config
        import sqlite3 as sqlite3_module
        # 从 SQLALCHEMY_DATABASE_URI 解析路径
        db_uri = Config.SQLALCHEMY_DATABASE_URI
        if db_uri.startswith('sqlite:///'):
            db_path = db_uri.replace('sqlite:///', '')
            # 处理绝对路径
            if db_path.startswith('/'):
                # Linux/Mac 绝对路径
                db_path = db_path
            else:
                # 相对路径，转换为绝对路径
                basedir = os.path.abspath(os.path.dirname(__file__))
                db_path = os.path.join(basedir, db_path)
        else:
            raise ValueError("不支持的数据库URI格式")
    except Exception as e:
        print(f"从配置文件获取数据库路径失败: {e}")
        # 回退到可能的路径列表
        possible_paths = [
            'app.db',  # 项目根目录
            'app/app.db',  # app目录下
            'data/app.db'  # data目录下（Docker环境）
        ]
        
        db_path = None
        for path in possible_paths:
            if os.path.exists(path):
                db_path = path
                print(f"找到数据库文件: {db_path}")
                break
        
        if not db_path:
            print(f"未找到数据库文件，尝试过的路径: {possible_paths}")
            return
    
    if not os.path.exists(db_path):
        print(f"数据库文件不存在: {db_path}")
        return
    
    print(f"使用数据库文件: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查列是否已存在
        cursor.execute("PRAGMA table_info(site_settings)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'ai_search_allow_anonymous' in columns:
            print("字段 ai_search_allow_anonymous 已存在，跳过添加")
            conn.close()
            return
        
        # 添加新列
        cursor.execute("""
            ALTER TABLE site_settings 
            ADD COLUMN ai_search_allow_anonymous BOOLEAN DEFAULT 0
        """)
        
        conn.commit()
        print("✅ 成功添加字段 ai_search_allow_anonymous")
        
        # 验证
        cursor.execute("PRAGMA table_info(site_settings)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'ai_search_allow_anonymous' in columns:
            print("✅ 字段验证成功")
        else:
            print("❌ 字段验证失败")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ 添加字段失败: {str(e)}")
        if conn:
            conn.close()

if __name__ == '__main__':
    add_column()

