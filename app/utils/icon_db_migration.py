#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图标系统二期数据库迁移工具。"""

from __future__ import annotations

import sqlite3
from typing import Iterable
from urllib.parse import urlparse


ICON_SETTINGS_FIELDS = [
    ('icon_display_mode', "VARCHAR(32) DEFAULT 'smart'"),
    ('icon_auto_fetch_on_create', 'BOOLEAN DEFAULT 0'),
    ('icon_default_sync_local', 'BOOLEAN DEFAULT 0'),
    ('icon_default_sync_imagebed', 'BOOLEAN DEFAULT 0'),
    ('icon_source_providers_json', 'TEXT'),
    ('icon_imagebed_provider', 'VARCHAR(64)'),
    ('icon_imagebed_api_url', 'VARCHAR(512)'),
    ('icon_imagebed_token', 'VARCHAR(512)'),
]

WEBSITE_ICON_FIELDS = [
    ('source_provider_override', "VARCHAR(64) DEFAULT 'inherit'"),
]


def _extract_domain_key(url: str | None) -> str:
    if not url:
        return ''
    processed = url if '://' in url else f'https://{url}'
    parsed = urlparse(processed)
    host = (parsed.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    return host


def _table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _ensure_columns(cursor: sqlite3.Cursor, table_name: str, fields: Iterable[tuple[str, str]]) -> int:
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing = {row[1] for row in cursor.fetchall()}
    added = 0
    for name, definition in fields:
        if name in existing:
            continue
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")
        added += 1
    return added


def migrate_icon_management_tables(db_path: str) -> int:
    """
    创建图标系统所需表，并补齐 site_settings 新字段。

    Returns:
        迁移动作数量（新增字段数 + 补写记录数）
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS icon_asset (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_key VARCHAR(255),
                file_hash VARCHAR(64) UNIQUE,
                source_url VARCHAR(512),
                source_host VARCHAR(255),
                local_path VARCHAR(512),
                mime_type VARCHAR(128),
                imagebed_provider VARCHAR(64),
                imagebed_url VARCHAR(1024),
                imagebed_delete_url VARCHAR(1024),
                imagebed_payload_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS website_icon (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                website_id INTEGER NOT NULL UNIQUE,
                icon_asset_id INTEGER,
                domain_key VARCHAR(255),
                source_mode VARCHAR(32) DEFAULT 'auto',
                source_provider_override VARCHAR(64) DEFAULT 'inherit',
                display_mode_override VARCHAR(32) DEFAULT 'inherit',
                sync_local_mode VARCHAR(32) DEFAULT 'inherit',
                sync_imagebed_mode VARCHAR(32) DEFAULT 'inherit',
                fetch_status VARCHAR(32) DEFAULT 'pending',
                local_status VARCHAR(32) DEFAULT 'pending',
                imagebed_status VARCHAR(32) DEFAULT 'pending',
                last_fetch_at DATETIME,
                last_local_sync_at DATETIME,
                last_imagebed_sync_at DATETIME,
                last_error TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (website_id) REFERENCES website (id),
                FOREIGN KEY (icon_asset_id) REFERENCES icon_asset (id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS icon_sync_task (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type VARCHAR(64) NOT NULL,
                scope_type VARCHAR(32) DEFAULT 'all',
                params_json TEXT,
                status VARCHAR(32) DEFAULT 'pending',
                total INTEGER DEFAULT 0,
                processed INTEGER DEFAULT 0,
                success INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0,
                created_by_id INTEGER,
                started_at DATETIME,
                finished_at DATETIME,
                error_summary TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by_id) REFERENCES user (id)
            )
            """
        )

        change_count = 0

        if _table_exists(cursor, 'site_settings'):
            change_count += _ensure_columns(cursor, 'site_settings', ICON_SETTINGS_FIELDS)

        if _table_exists(cursor, 'website_icon'):
            change_count += _ensure_columns(cursor, 'website_icon', WEBSITE_ICON_FIELDS)

        if _table_exists(cursor, 'website'):
            cursor.execute("SELECT website_id FROM website_icon")
            existing_meta_ids = {row[0] for row in cursor.fetchall()}

            cursor.execute("SELECT id, url, icon FROM website")
            rows = cursor.fetchall()

            for website_id, url, icon in rows:
                if website_id in existing_meta_ids:
                    continue

                cursor.execute(
                    """
                    INSERT INTO website_icon (
                        website_id,
                        domain_key,
                        source_mode,
                        fetch_status,
                        local_status,
                        imagebed_status
                    ) VALUES (?, ?, 'auto', ?, 'pending', 'pending')
                    """,
                    (
                        website_id,
                        _extract_domain_key(url),
                        'success' if icon and str(icon).strip() else 'pending',
                    ),
                )
                change_count += 1

        conn.commit()
        conn.close()
        return change_count
    except Exception:
        return 0
