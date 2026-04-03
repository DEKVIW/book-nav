#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Legacy batch icon API backed by the v2 icon task system."""

from flask import jsonify
from flask_login import current_user, login_required

from app.admin import bp
from app.admin.decorators import superadmin_required
from app.models import IconSyncTask
from app.utils.icon_service import (
    ICON_TASK_SYNC_MISSING,
    recover_stale_icon_sync_tasks,
    serialize_icon_sync_task,
    start_icon_sync_task,
)


def _latest_icon_task():
    recover_stale_icon_sync_tasks()
    return IconSyncTask.query.order_by(IconSyncTask.created_at.desc()).first()


@bp.route('/api/batch-fetch-icons', methods=['POST'])
@login_required
@superadmin_required
def batch_fetch_icons():
    task, started = start_icon_sync_task(ICON_TASK_SYNC_MISSING, created_by_id=current_user.id)
    return jsonify({
        'success': started or task is not None,
        'message': '图标任务已启动' if started else '已有图标任务正在执行，请等待完成',
        'task_id': task.id if task else None,
    })


@bp.route('/api/batch-fetch-icons/status')
@login_required
@superadmin_required
def batch_fetch_icons_status():
    """获取兼容旧版页面的任务状态。"""
    serialized = serialize_icon_sync_task(_latest_icon_task())
    response = jsonify({
        'is_running': serialized['is_running'],
        'total': serialized['total'],
        'processed': serialized['processed'],
        'success': serialized['success'],
        'failed': serialized['failed'],
        'elapsed_time': serialized['elapsed_time'],
        'percent': serialized['percent'],
    })
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@bp.route('/api/batch-fetch-icons/stop', methods=['POST'])
@login_required
@superadmin_required
def batch_fetch_icons_stop():
    task = _latest_icon_task()
    if not task or task.status not in {'pending', 'running'}:
        return jsonify({'success': False, 'message': '当前没有正在执行的图标任务'})
    return jsonify({
        'success': True,
        'message': '当前版本暂不支持中止任务，请等待执行完成',
    })


def process_missing_icons(app):
    """保留旧函数名，避免历史导入报错。"""
    return None

