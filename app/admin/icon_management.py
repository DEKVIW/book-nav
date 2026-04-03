#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图标系统二期后台管理。"""

from __future__ import annotations

from flask import jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required

from app import db
from app.admin import bp
from app.admin.decorators import superadmin_required
from app.models import IconSyncTask, SiteSettings, Website
from app.utils.icon_service import (
    ICON_TASK_RETRY_FAILED,
    ICON_TASK_SYNC_IMAGEBED,
    ICON_TASK_SYNC_LOCAL,
    ICON_TASK_SYNC_MISSING,
    get_icon_dashboard_summary,
    get_website_icon_snapshot,
    recover_stale_icon_sync_tasks,
    serialize_icon_sync_task,
    start_icon_sync_task,
)


TASK_LABELS = {
    ICON_TASK_SYNC_MISSING: '补齐缺失图标',
    ICON_TASK_SYNC_LOCAL: '同步到本地',
    ICON_TASK_SYNC_IMAGEBED: '同步到图床',
    ICON_TASK_RETRY_FAILED: '重试失败项',
}


def _mask_token(token: str | None) -> str:
    token = (token or '').strip()
    if not token:
        return ''
    if len(token) <= 8:
        return '*' * len(token)
    return f'{token[:4]}{"*" * (len(token) - 8)}{token[-4:]}'


def _collect_problem_sites(limit: int = 20) -> list[dict]:
    sites = []
    recent_websites = Website.query.order_by(Website.created_at.desc()).limit(200).all()
    for website in recent_websites:
        snapshot = get_website_icon_snapshot(website)
        if (
            not snapshot.get('url')
            or snapshot.get('fetch_status') == 'failed'
            or snapshot.get('local_status') == 'failed'
            or snapshot.get('imagebed_status') == 'failed'
        ):
            sites.append({'website': website, 'snapshot': snapshot})
        if len(sites) >= limit:
            break
    return sites


@bp.route('/icon-management', methods=['GET', 'POST'])
@login_required
@superadmin_required
def icon_management():
    recover_stale_icon_sync_tasks()
    settings = SiteSettings.get_settings()

    if request.method == 'POST':
        settings.icon_display_mode = request.form.get('icon_display_mode', 'smart')
        settings.icon_auto_fetch_on_create = bool(request.form.get('icon_auto_fetch_on_create'))
        settings.icon_default_sync_local = bool(request.form.get('icon_default_sync_local'))
        settings.icon_default_sync_imagebed = bool(request.form.get('icon_default_sync_imagebed'))
        settings.icon_imagebed_provider = (request.form.get('icon_imagebed_provider') or '').strip() or None
        settings.icon_imagebed_api_url = (request.form.get('icon_imagebed_api_url') or '').strip() or None

        token_input = (request.form.get('icon_imagebed_token') or '').strip()
        if token_input and '*' not in token_input:
            settings.icon_imagebed_token = token_input
        if request.form.get('clear_icon_imagebed_token'):
            settings.icon_imagebed_token = None

        db.session.commit()
        flash('图标管理设置已更新', 'success')
        return redirect(url_for('admin.icon_management'))

    recent_tasks = IconSyncTask.query.order_by(IconSyncTask.created_at.desc()).limit(10).all()

    return render_template(
        'admin/icon_management.html',
        title='图标管理',
        settings=settings,
        summary=get_icon_dashboard_summary(),
        recent_tasks=[serialize_icon_sync_task(task) | {'label': TASK_LABELS.get(task.task_type, task.task_type)} for task in recent_tasks],
        latest_task=serialize_icon_sync_task(recent_tasks[0] if recent_tasks else None),
        problem_sites=_collect_problem_sites(),
        task_labels=TASK_LABELS,
        masked_token=_mask_token(settings.icon_imagebed_token),
    )


@bp.route('/api/icon-management/task/start', methods=['POST'])
@login_required
@superadmin_required
def api_start_icon_task():
    data = request.get_json(silent=True) or {}
    task_type = data.get('task_type') or data.get('task') or ICON_TASK_SYNC_MISSING
    if task_type not in TASK_LABELS:
        return jsonify({'success': False, 'message': '不支持的任务类型'}), 400

    task, started = start_icon_sync_task(task_type, created_by_id=current_user.id)
    message = '任务已启动' if started else '已有图标任务正在执行'
    return jsonify({
        'success': True,
        'started': started,
        'message': message,
        'task': serialize_icon_sync_task(task) | {'label': TASK_LABELS.get(task.task_type, task.task_type)},
    })


@bp.route('/api/icon-management/task/<int:task_id>')
@login_required
@superadmin_required
def api_get_icon_task(task_id: int):
    recover_stale_icon_sync_tasks()
    task = IconSyncTask.query.get_or_404(task_id)
    return jsonify({
        'success': True,
        'task': serialize_icon_sync_task(task) | {'label': TASK_LABELS.get(task.task_type, task.task_type)},
    })


@bp.route('/api/icon-management/task/latest')
@login_required
@superadmin_required
def api_get_latest_icon_task():
    recover_stale_icon_sync_tasks()
    task = IconSyncTask.query.order_by(IconSyncTask.created_at.desc()).first()
    return jsonify({
        'success': True,
        'task': serialize_icon_sync_task(task) | {'label': TASK_LABELS.get(task.task_type, task.task_type)} if task else None,
    })
