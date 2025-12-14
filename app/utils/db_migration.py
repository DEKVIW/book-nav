#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据库迁移工具 - 统一处理字段添加"""

import sqlite3
from typing import List, Tuple


def migrate_site_settings_fields(db_path: str) -> int:
    """
    检查并添加 site_settings 表的缺失字段
    
    Args:
        db_path: 数据库文件路径
        
    Returns:
        添加的字段数量
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查 site_settings 表的字段
        cursor.execute("PRAGMA table_info(site_settings)")
        columns = cursor.fetchall()
        column_names = [column[1] for column in columns]
        
        # AI 搜索配置字段
        ai_fields = [
            ('ai_search_enabled', 'BOOLEAN DEFAULT 0'),
            ('ai_search_allow_anonymous', 'BOOLEAN DEFAULT 0'),
            ('ai_api_base_url', 'VARCHAR(512)'),
            ('ai_api_key', 'VARCHAR(512)'),
            ('ai_model_name', 'VARCHAR(128)'),
            ('ai_temperature', 'REAL DEFAULT 0.7'),
            ('ai_max_tokens', 'INTEGER DEFAULT 500')
        ]
        
        # 向量搜索配置字段
        vector_fields = [
            ('vector_search_enabled', 'BOOLEAN DEFAULT 0'),
            ('qdrant_url', 'VARCHAR(512) DEFAULT \'http://localhost:6333\''),
            ('embedding_model', 'VARCHAR(128) DEFAULT \'text-embedding-3-small\''),
            ('vector_similarity_threshold', 'REAL DEFAULT 0.3'),
            ('vector_max_results', 'INTEGER DEFAULT 50'),
            # 新增：独立的 Embedding API 配置
            ('embedding_api_base_url', 'VARCHAR(512)'),
            ('embedding_api_key', 'VARCHAR(512)')
        ]
        
        # 过渡页设置字段
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
        
        # 公告设置字段
        announcement_fields = [
            ('announcement_enabled', 'BOOLEAN DEFAULT 0'),
            ('announcement_title', 'VARCHAR(128)'),
            ('announcement_content', 'TEXT'),
            ('announcement_start', 'DATETIME'),
            ('announcement_end', 'DATETIME'),
            ('announcement_remember_days', 'INTEGER DEFAULT 7')
        ]
        
        # PC/移动端背景字段
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
                    added_count += 1
                except sqlite3.Error as e:
                    # 如果字段已存在或其他错误，记录但不中断
                    pass
        
        if added_count > 0:
            conn.commit()
        
        conn.close()
        return added_count
    except Exception as e:
        # 迁移失败时返回0，不中断应用启动
        return 0

