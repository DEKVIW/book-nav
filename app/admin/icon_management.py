#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图标系统二期后台管理。"""

from __future__ import annotations

import json

from flask import jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required

from app import db
from app.admin import bp
from app.admin.decorators import superadmin_required
from app.models import IconSyncTask, SiteSettings, Website, WebsiteIcon
from app.main.utils import merge_icon_source_providers
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import joinedload
from app.utils.icon_service import (
    ICON_TASK_REFRESH_SOURCE,
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
    ICON_TASK_REFRESH_SOURCE: '按当前规则刷新源地址',
    ICON_TASK_SYNC_LOCAL: '同步到本地',
    ICON_TASK_SYNC_IMAGEBED: '同步到图床',
    ICON_TASK_RETRY_FAILED: '重试失败项',
}


def _is_sqlite_locked_error(exc: Exception) -> bool:
    return 'database is locked' in str(exc).lower()


def _safe_provider_order(value, default: int = 999) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_source_providers(payload_text: str, current_providers: list[dict]) -> tuple[list[dict], list[str]]:
    provider_map = {
        provider.get('id'): provider.copy()
        for provider in current_providers
        if provider.get('id')
    }
    normalized: list[dict] = []
    errors: list[str] = []
    seen_ids: set[str] = set()

    try:
        payload = json.loads(payload_text) if payload_text else []
    except Exception:
        return current_providers, ['自定义图标源配置格式无效，已保留原有设置']

    if not isinstance(payload, list):
        return current_providers, ['自定义图标源配置格式无效，已保留原有设置']

    for item in payload:
        if not isinstance(item, dict):
            continue

        provider_id = str(item.get('id') or '').strip()
        if not provider_id:
            errors.append('存在缺少 ID 的图标源配置，已忽略')
            continue
        if provider_id in seen_ids:
            errors.append(f'图标源 ID 重复：{provider_id}，已忽略重复项')
            continue

        current_provider = provider_map.get(provider_id)
        is_builtin = bool(item.get('builtin'))
        if current_provider and current_provider.get('builtin'):
            is_builtin = True

        if is_builtin:
            if not current_provider:
                continue
            provider = current_provider.copy()
            provider['enabled'] = bool(item.get('enabled', provider.get('enabled', True)))
            provider['order'] = _safe_provider_order(item.get('order'), provider.get('order', 999))
            normalized.append(provider)
            seen_ids.add(provider_id)
            continue

        label = str(item.get('label') or '').strip()
        template = str(item.get('template') or '').strip()
        description = str(item.get('description') or '').strip()
        order = _safe_provider_order(item.get('order'), current_provider.get('order', 999) if current_provider else 999)
        enabled = bool(item.get('enabled', True))

        validation_errors: list[str] = []
        if not label:
            validation_errors.append('名称不能为空')
        if not template:
            validation_errors.append('模板不能为空')
        elif '{domain}' not in template:
            validation_errors.append('模板必须包含 {domain} 占位符')
        elif not template.startswith(('http://', 'https://')):
            validation_errors.append('模板必须以 http:// 或 https:// 开头')

        if validation_errors:
            if current_provider and not current_provider.get('builtin'):
                normalized.append(current_provider.copy())
                seen_ids.add(provider_id)
                errors.append(
                    f'自定义图标源“{current_provider.get("label") or provider_id}”校验失败，已保留原有配置：'
                    + '，'.join(validation_errors)
                )
            else:
                errors.append(f'自定义图标源“{label or provider_id}”未保存：' + '，'.join(validation_errors))
            continue

        normalized.append({
            'id': provider_id,
            'label': label[:80],
            'kind': 'proxy',
            'builtin': False,
            'enabled': enabled,
            'order': order,
            'supports_download': True,
            'description': description[:255],
            'template': template[:1024],
        })
        seen_ids.add(provider_id)

    for provider in current_providers:
        provider_id = provider.get('id')
        if provider.get('builtin') and provider_id and provider_id not in seen_ids:
            normalized.append(provider.copy())

    normalized.sort(key=lambda item: (int(item.get('order', 999)), item.get('label') or item.get('id') or ''))
    return normalized, errors


def _collect_problem_sites(limit: int = 80) -> list[dict]:
    sites = []
    recent_websites = (
        Website.query.options(
            joinedload(Website.icon_meta).joinedload(WebsiteIcon.icon_asset)
        )
        .order_by(Website.created_at.desc())
        .limit(200)
        .all()
    )
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
        trigger_refresh_source = bool(request.form.get('refresh_auto_source_urls'))
        settings.icon_display_mode = request.form.get('icon_display_mode', 'smart')
        settings.icon_auto_fetch_on_create = bool(request.form.get('icon_auto_fetch_on_create'))
        settings.icon_default_sync_local = bool(request.form.get('icon_default_sync_local'))
        settings.icon_default_sync_imagebed = bool(request.form.get('icon_default_sync_imagebed'))
        settings.icon_imagebed_provider = (request.form.get('icon_imagebed_provider') or '').strip() or None

        api_url_input = (request.form.get('icon_imagebed_api_url') or '').strip()
        if api_url_input:
            settings.icon_imagebed_api_url = api_url_input
        if request.form.get('clear_icon_imagebed_api_url'):
            settings.icon_imagebed_api_url = None

        token_input = (request.form.get('icon_imagebed_token') or '').strip()
        if token_input:
            settings.icon_imagebed_token = token_input
        if request.form.get('clear_icon_imagebed_token'):
            settings.icon_imagebed_token = None

        current_providers = settings.get_icon_source_providers()
        providers_payload = request.form.get('icon_source_providers_payload')
        if (providers_payload or '').strip():
            updated_providers, provider_errors = _normalize_source_providers(providers_payload, current_providers)
        else:
            provider_errors = []
            updated_providers = current_providers
        if provider_errors:
            flash('；'.join(provider_errors), 'warning')

        settings.set_icon_source_providers(updated_providers)

        db.session.commit()
        if trigger_refresh_source:
            task, started = start_icon_sync_task(ICON_TASK_REFRESH_SOURCE, created_by_id=current_user.id)
            if started:
                flash('已按当前规则启动自动源地址刷新任务', 'info')
            else:
                flash('已有图标任务正在执行，自动源地址刷新未重复启动', 'warning')
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
        source_providers=merge_icon_source_providers(settings.icon_source_providers_json),
        task_labels=TASK_LABELS,
        has_imagebed_api_url=bool((settings.icon_imagebed_api_url or '').strip()),
        has_imagebed_token=bool((settings.icon_imagebed_token or '').strip()),
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


@bp.route('/api/icon-management/task/<int:task_id>/delete', methods=['POST'])
@login_required
@superadmin_required
def api_delete_icon_task(task_id: int):
    recover_stale_icon_sync_tasks()
    task = IconSyncTask.query.get_or_404(task_id)
    if task.status in {'pending', 'running'}:
        return jsonify({'success': False, 'message': '运行中的任务不能删除'}), 400

    try:
        db.session.delete(task)
        db.session.commit()
    except OperationalError as exc:
        db.session.rollback()
        if _is_sqlite_locked_error(exc):
            return jsonify({'success': False, 'message': '数据库正忙，请稍后再删除任务记录'}), 409
        raise
    return jsonify({'success': True, 'message': '任务记录已删除'})


@bp.route('/api/icon-management/task/clear', methods=['POST'])
@login_required
@superadmin_required
def api_clear_icon_tasks():
    recover_stale_icon_sync_tasks()
    active_task = (
        IconSyncTask.query.filter(IconSyncTask.status.in_(['pending', 'running']))
        .order_by(IconSyncTask.created_at.desc())
        .first()
    )
    if active_task:
        return jsonify({'success': False, 'message': '当前有任务正在执行，请等待任务结束后再清空记录'}), 409

    try:
        cleared = (
            IconSyncTask.query.filter(IconSyncTask.status.notin_(['pending', 'running']))
            .delete(synchronize_session=False)
        )
        db.session.commit()
    except OperationalError as exc:
        db.session.rollback()
        if _is_sqlite_locked_error(exc):
            return jsonify({'success': False, 'message': '数据库正忙，请稍后再清空任务记录'}), 409
        raise
    return jsonify({'success': True, 'message': f'已删除 {cleared} 条任务记录'})
