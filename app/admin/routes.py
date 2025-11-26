#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""管理面板首页路由"""

from datetime import datetime
from flask import render_template
from flask_login import login_required
from app.admin import bp
from app.admin.decorators import admin_required
from app.models import User, Category, Website, InvitationCode


@bp.route('/')
@login_required
@admin_required
def index():
    """管理面板首页"""
    stats = {
        'users': User.query.count(),
        'active_users': User.query.filter(User.created_at > datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)).count(),
        'categories': Category.query.count(),
        'websites': Website.query.count(),
        'invitation_codes': InvitationCode.query.filter_by(is_active=True, used_by_id=None).count()
    }
    return render_template('admin/index.html', title='管理面板', stats=stats)

# 注意：所有其他路由已拆分到以下模块：
# - categories.py: 分类管理
# - websites.py: 网站管理
# - invitations.py: 邀请码管理
# - users.py: 用户管理
# - site_settings.py: 站点设置
# - operation_logs.py: 操作日志
# - data_management.py: 数据管理（导入导出）
# - backups.py: 备份管理
# - icon_fetch.py: 图标批量抓取
# - wallpapers.py: 背景管理
# - deadlinks.py: 死链检测
