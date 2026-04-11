#!/usr/bin/env python
# -*- coding: utf-8 -*-

from datetime import datetime
import json
from typing import Dict, Optional

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app import db
from app.admin import bp
from app.admin.decorators import superadmin_required
from app.admin.forms import SiteSettingsForm
from app.admin.utils import save_image
from app.models import AIProviderConfig, SiteSettings
from app.utils.ai_model_discovery import discover_and_probe_models
from app.utils.ai_search import (
    AI_INTERFACE_MODE_AUTO,
    AISearchService,
    AICompatibilityError,
    AIEmptyResponseError,
    AIJSONParseError,
    AI_TASK_KEYS,
    create_ai_service_from_settings,
    get_ai_model_for_task,
    resolve_ai_service_candidates,
)


AI_TASK_LABELS = {
    'intent': '搜索意图分析',
    'rerank': '搜索结果重排',
    'translate': '翻译',
    'site_info': '网站信息补全',
}


def _format_ai_structured_output_error(error: Exception) -> str:
    if isinstance(error, AIEmptyResponseError):
        reason = f'接口已连通，但模型返回了空内容或非直接文本内容：{str(error)}'
        suggestion = '请优先选择会直接输出文本内容的聊天模型，并关闭工具调用或仅推理模式；必要时切换接口模式为自动兜底。'
    elif isinstance(error, AIJSONParseError):
        reason = f'接口已连通，但结构化 JSON 输出解析失败：{str(error)}'
        suggestion = '请使用能稳定输出 JSON 的模型，或切换接口模式为自动兜底 / Responses。'
    elif isinstance(error, AICompatibilityError):
        reason = f'接口已连通，但返回格式与项目当前能力不兼容：{str(error)}'
        suggestion = '请确认当前模型能直接输出文本或 JSON，避免仅返回 tool_calls 等结构。'
    else:
        reason = f'接口已连通，但结构化功能测试失败：{str(error)}'
        suggestion = '请检查当前模型是否适合稳定文本 / JSON 输出。'
    return f'❌ AI 接口连通成功，但项目结构化功能测试失败\n原因：{reason}\n建议：{suggestion}'


def _parse_probe_datetime(value: str) -> Optional[datetime]:
    value = (value or '').strip()
    if not value:
        return None
    normalized = value.replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _json_or_default(raw_value, default):
    if not raw_value:
        return default
    if isinstance(raw_value, (dict, list)):
        return raw_value
    try:
        return json.loads(raw_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _provider_to_public_dict(provider: AIProviderConfig) -> dict:
    data = provider.to_dict(include_catalog=True)
    return data


def _candidate_to_public(candidate: dict) -> dict:
    return {
        'provider_id': candidate.get('provider_id'),
        'provider_name': candidate.get('provider_name', ''),
        'model_name': candidate.get('model_name', ''),
        'interface_mode': candidate.get('interface_mode', ''),
        'source': candidate.get('source', ''),
    }


def _build_ai_management_state(settings: SiteSettings) -> dict:
    providers = [_provider_to_public_dict(provider) for provider in settings.get_ai_providers(enabled_only=False)]
    task_bindings = settings.get_ai_task_bindings()
    task_test_results = settings.get_ai_task_test_results()
    effective_tasks = {}
    for task in AI_TASK_KEYS:
        candidates = resolve_ai_service_candidates(settings, task=task)
        effective_tasks[task] = _candidate_to_public(candidates[0]) if candidates else {}
    return {
        'providers': providers,
        'task_bindings': task_bindings,
        'task_test_results': task_test_results,
        'effective_tasks': effective_tasks,
        'summary': {
            'provider_count': len(providers),
            'enabled_provider_count': sum(1 for item in providers if item.get('enabled')),
            'detected_provider_count': sum(1 for item in providers if item.get('catalog')),
        },
    }


def _normalize_provider_payload(data: dict) -> dict:
    provider_id = data.get('id')
    try:
        provider_id = int(provider_id) if provider_id not in (None, '', False) else None
    except (TypeError, ValueError):
        provider_id = None

    priority = data.get('priority', 100)
    try:
        priority = int(priority)
    except (TypeError, ValueError):
        priority = 100

    return {
        'id': provider_id,
        'name': (data.get('name') or '').strip() or '默认 AI 提供方',
        'api_base_url': (data.get('api_base_url') or '').strip(),
        'api_key': (data.get('api_key') or '').strip(),
        'interface_mode': (data.get('interface_mode') or AI_INTERFACE_MODE_AUTO).strip().lower(),
        'enabled': bool(data.get('enabled', True)),
        'priority': priority,
    }


def _first_provider_model(provider: AIProviderConfig) -> str:
    recommended = provider.get_recommended_models()
    for key in ('fallback', 'intent', 'rerank', 'translate', 'site_info'):
        if recommended.get(key):
            return recommended[key]
    catalog = provider.get_model_catalog() or []
    for item in catalog:
        if isinstance(item, dict) and item.get('id'):
            return item['id']
    return ''


def _test_provider_model(provider: AIProviderConfig, model_name: str) -> dict:
    if not all([(provider.api_base_url or '').strip(), (provider.api_key or '').strip(), (model_name or '').strip()]):
        raise ValueError('请先填写完整的提供方 URL、密钥和模型。')

    ai_service = AISearchService(
        api_base_url=provider.api_base_url,
        api_key=provider.api_key,
        model_name=model_name,
        interface_mode=provider.interface_mode or AI_INTERFACE_MODE_AUTO,
    )
    transport_result = ai_service.probe_text_output()
    structured_result = ai_service.analyze_search_intent('测试')
    return {
        'transport_result': transport_result,
        'structured_result': structured_result,
        'service': ai_service,
    }


def _sync_provider_backfill(settings: SiteSettings) -> None:
    settings.sync_legacy_ai_fields_from_providers()


def _build_task_test_result(task: str, status: str, message: str, service=None) -> dict:
    return {
        'status': status,
        'message': message,
        'provider_id': getattr(service, 'provider_id', None) if service else None,
        'provider_name': getattr(service, 'provider_name', '') if service else '',
        'model_name': getattr(service, 'model_name', '') if service else '',
        'tested_at': datetime.utcnow().isoformat(),
        'protocol': getattr(service, 'last_protocol_used', '') if service else '',
    }


def _run_task_test(task: str, settings: SiteSettings):
    service = create_ai_service_from_settings(settings, require_enabled=False, task=task)
    if not service:
        return _build_task_test_result(task, 'error', '未找到可用的提供方或模型。')

    try:
        if task == 'intent':
            result = service.analyze_search_intent('帮我找 AI 文档站点')
            if not isinstance(result, dict) or not result.get('intent'):
                raise AIJSONParseError('意图分析未返回有效结构化结果')
            message = '意图分析测试通过'
        elif task == 'rerank':
            result = service.recommend_websites(
                'AI 文档',
                {'intent': '查找 AI 文档', 'keywords': ['AI', '文档'], 'related_terms': [], 'category_hints': []},
                [
                    {'id': 1, 'title': 'OpenAI Docs', 'description': 'OpenAI 官方文档', 'category': '开发', 'url': 'https://platform.openai.com/docs'},
                    {'id': 2, 'title': 'Qdrant', 'description': '向量数据库文档', 'category': '开发', 'url': 'https://qdrant.tech'},
                ],
                max_recommendations=1,
            )
            if not isinstance(result, dict) or 'recommendations' not in result:
                raise AIJSONParseError('重排未返回有效推荐结果')
            message = '搜索重排测试通过'
        elif task == 'translate':
            result = service.translate_text('Hello world', target_lang='zh')
            if not isinstance(result, str) or not result.strip():
                raise AIEmptyResponseError('翻译结果为空')
            message = '翻译测试通过'
        else:
            result = service.generate_website_info('https://example.com')
            if not isinstance(result, dict) or (not result.get('title') and not result.get('description')):
                raise AIJSONParseError('网站信息补全未返回有效结果')
            message = '网站信息补全测试通过'
        return _build_task_test_result(task, 'success', message, service=service)
    except Exception as exc:
        return _build_task_test_result(task, 'error', str(exc), service=service)


@bp.route('/site-settings', methods=['GET', 'POST'])
@login_required
@superadmin_required
def site_settings():
    try:
        settings = SiteSettings.get_settings()
        form = SiteSettingsForm(obj=settings)

        if request.method == 'GET' and settings.embedding_api_key:
            if len(settings.embedding_api_key) > 8:
                masked_key = settings.embedding_api_key[:4] + '*' * (len(settings.embedding_api_key) - 8) + settings.embedding_api_key[-4:]
            else:
                masked_key = '*' * len(settings.embedding_api_key)
            form.embedding_api_key.data = masked_key
            form.embedding_api_key.raw_data = [masked_key]

        if request.method == 'GET' and not settings.qdrant_url:
            form.qdrant_url.data = SiteSettings.get_default_qdrant_url()

        if form.validate_on_submit():
            if form.logo_file.data:
                logo_filename = save_image(form.logo_file.data, 'logos')
                if logo_filename:
                    settings.site_logo = url_for('static', filename=f'uploads/logos/{logo_filename}')
            elif form.site_logo.data:
                settings.site_logo = form.site_logo.data
            elif not form.site_logo.data and 'clear_logo' in request.form:
                settings.site_logo = None

            if form.favicon_file.data:
                favicon_filename = save_image(form.favicon_file.data, 'favicons')
                if favicon_filename:
                    settings.site_favicon = url_for('static', filename=f'uploads/favicons/{favicon_filename}')
            elif form.site_favicon.data:
                settings.site_favicon = form.site_favicon.data
            elif not form.site_favicon.data and 'clear_favicon' in request.form:
                settings.site_favicon = None

            if form.background_file.data and form.background_type.data == 'image':
                bg_filename = save_image(form.background_file.data, 'backgrounds')
                if bg_filename:
                    settings.background_url = url_for('static', filename=f'uploads/backgrounds/{bg_filename}')
            elif form.background_url.data:
                settings.background_url = form.background_url.data

            settings.site_name = form.site_name.data
            settings.site_subtitle = form.site_subtitle.data
            settings.site_keywords = form.site_keywords.data
            settings.site_description = form.site_description.data
            settings.footer_content = form.footer_content.data
            settings.background_type = form.background_type.data

            if form.pc_background_file.data and form.pc_background_type.data == 'image':
                pc_bg_filename = save_image(form.pc_background_file.data, 'backgrounds')
                if pc_bg_filename:
                    settings.pc_background_url = url_for('static', filename=f'uploads/backgrounds/{pc_bg_filename}')
            elif form.pc_background_url.data:
                settings.pc_background_url = form.pc_background_url.data
            settings.pc_background_type = form.pc_background_type.data

            if form.mobile_background_file.data and form.mobile_background_type.data == 'image':
                mobile_bg_filename = save_image(form.mobile_background_file.data, 'backgrounds')
                if mobile_bg_filename:
                    settings.mobile_background_url = url_for('static', filename=f'uploads/backgrounds/{mobile_bg_filename}')
            elif form.mobile_background_url.data:
                settings.mobile_background_url = form.mobile_background_url.data
            settings.mobile_background_type = form.mobile_background_type.data

            settings.enable_transition = form.enable_transition.data
            settings.transition_time = form.transition_time.data
            settings.admin_transition_time = form.admin_transition_time.data
            settings.transition_ad1 = form.transition_ad1.data
            settings.transition_ad2 = form.transition_ad2.data
            settings.transition_remember_choice = form.transition_remember_choice.data
            settings.transition_show_description = form.transition_show_description.data
            settings.transition_theme = form.transition_theme.data
            settings.transition_color = form.transition_color.data

            settings.announcement_enabled = form.announcement_enabled.data
            settings.announcement_title = form.announcement_title.data
            settings.announcement_content = form.announcement_content.data
            settings.announcement_start = form.announcement_start.data
            settings.announcement_end = form.announcement_end.data
            settings.announcement_remember_days = form.announcement_remember_days.data

            settings.ai_search_enabled = form.ai_search_enabled.data
            settings.ai_search_allow_anonymous = form.ai_search_allow_anonymous.data
            try:
                settings.ai_temperature = float(form.ai_temperature.data) if form.ai_temperature.data else 0.7
            except (TypeError, ValueError):
                settings.ai_temperature = 0.7
            settings.ai_max_tokens = form.ai_max_tokens.data if form.ai_max_tokens.data else 500
            settings.set_ai_task_bindings(_json_or_default(request.form.get('ai_task_bindings_json'), settings.get_ai_task_bindings()))
            settings.set_ai_task_test_results(_json_or_default(request.form.get('ai_task_test_results_json'), settings.get_ai_task_test_results()))
            _sync_provider_backfill(settings)

            settings.vector_search_enabled = form.vector_search_enabled.data
            settings.embedding_api_base_url = form.embedding_api_base_url.data if form.embedding_api_base_url.data else None
            embedding_api_key_input = form.embedding_api_key.data.strip() if form.embedding_api_key.data else ''
            is_masked = embedding_api_key_input and ('*' in embedding_api_key_input or (len(embedding_api_key_input) < 20 and embedding_api_key_input.count('*') > 0))
            if embedding_api_key_input and not is_masked:
                settings.embedding_api_key = embedding_api_key_input
            if form.qdrant_url.data:
                settings.qdrant_url = form.qdrant_url.data.strip().rstrip('/')
            else:
                settings.qdrant_url = SiteSettings.get_default_qdrant_url()
            settings.embedding_model = form.embedding_model.data if form.embedding_model.data else 'text-embedding-3-small'
            try:
                settings.vector_similarity_threshold = float(form.vector_similarity_threshold.data) if form.vector_similarity_threshold.data else 0.3
            except (TypeError, ValueError):
                settings.vector_similarity_threshold = 0.3
            settings.vector_max_results = form.vector_max_results.data if form.vector_max_results.data else 50

            ai_configured = bool(get_ai_model_for_task(settings) and (settings.get_primary_ai_provider(enabled_only=True) or settings.ai_api_base_url))
            embedding_api_url, embedding_api_key = settings.get_embedding_api_config()
            vector_configured = all([
                settings.vector_search_enabled,
                settings.qdrant_url,
                settings.embedding_model,
                embedding_api_url,
                embedding_api_key,
            ])

            try:
                db.session.commit()
                db.session.refresh(settings)
                if vector_configured:
                    flash('站点设置已更新，向量搜索配置可用', 'success')
                elif ai_configured and settings.ai_search_enabled:
                    flash('站点设置已更新，AI 搜索配置可用并已启用', 'success')
                elif ai_configured:
                    flash('站点设置已更新，AI 配置已保存', 'success')
                else:
                    flash('站点设置已更新', 'success')
                return redirect(url_for('admin.site_settings'))
            except Exception as exc:
                db.session.rollback()
                flash(f'保存设置失败: {str(exc)}', 'danger')
                current_app.logger.error(f'保存站点设置失败: {str(exc)}')

        return render_template(
            'admin/site_settings.html',
            title='站点设置',
            form=form,
            settings=settings,
            ai_management_state=_build_ai_management_state(settings),
            vector_indexing_available='admin.batch_generate_vectors' in current_app.view_functions,
        )
    except Exception as exc:
        flash(f'加载站点设置失败: {str(exc)}', 'danger')
        current_app.logger.error(f'加载站点设置失败: {str(exc)}')
        return redirect(url_for('admin.index'))


@bp.route('/api/ai-providers/save', methods=['POST'])
@login_required
@superadmin_required
def save_ai_provider():
    data = _normalize_provider_payload(request.get_json(silent=True) or {})
    provider = AIProviderConfig.query.get(data['id']) if data['id'] else AIProviderConfig()
    if provider is None:
        return jsonify({'success': False, 'message': '提供方不存在'}), 404

    if not data['api_base_url']:
        return jsonify({'success': False, 'message': '请填写基础 URL'}), 400
    if data['interface_mode'] not in {'auto', 'chat', 'responses'}:
        data['interface_mode'] = AI_INTERFACE_MODE_AUTO

    api_key_input = data['api_key']
    keep_existing_key = bool(provider.id and (not api_key_input or '*' in api_key_input))
    if not provider.id and not api_key_input:
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400

    config_changed = (
        (provider.api_base_url or '') != data['api_base_url'] or
        (provider.interface_mode or AI_INTERFACE_MODE_AUTO) != data['interface_mode'] or
        (not keep_existing_key and (provider.api_key or '') != api_key_input)
    )

    provider.name = data['name']
    provider.api_base_url = data['api_base_url']
    provider.interface_mode = data['interface_mode']
    provider.enabled = data['enabled']
    provider.priority = data['priority']
    if not keep_existing_key:
        provider.api_key = api_key_input
    if config_changed:
        provider.clear_probe_data()

    db.session.add(provider)
    db.session.flush()
    settings = SiteSettings.get_settings()
    _sync_provider_backfill(settings)
    db.session.commit()
    return jsonify({'success': True, 'message': '提供方已保存', 'provider': provider.to_dict(include_catalog=True), 'state': _build_ai_management_state(settings)})


@bp.route('/api/ai-providers/<int:provider_id>/delete', methods=['POST'])
@login_required
@superadmin_required
def delete_ai_provider(provider_id: int):
    provider = AIProviderConfig.query.get_or_404(provider_id)
    settings = SiteSettings.get_settings()
    bindings = settings.get_ai_task_bindings()
    results = settings.get_ai_task_test_results()

    for task_key, binding in bindings.items():
        if binding.get('provider_id') == provider_id:
            binding['mode'] = 'auto'
            binding['provider_id'] = None
            binding['model_name'] = ''
    for task_key, result in results.items():
        if result.get('provider_id') == provider_id:
            results[task_key] = {
                'status': 'idle',
                'message': '',
                'provider_id': None,
                'provider_name': '',
                'model_name': '',
                'tested_at': '',
                'protocol': '',
            }

    settings.set_ai_task_bindings(bindings)
    settings.set_ai_task_test_results(results)
    db.session.delete(provider)
    _sync_provider_backfill(settings)
    db.session.commit()
    return jsonify({'success': True, 'message': '提供方已删除', 'state': _build_ai_management_state(settings)})


@bp.route('/api/ai-providers/<int:provider_id>/detect', methods=['POST'])
@login_required
@superadmin_required
def detect_ai_provider_models(provider_id: int):
    provider = AIProviderConfig.query.get_or_404(provider_id)
    if not all([(provider.api_base_url or '').strip(), (provider.api_key or '').strip()]):
        return jsonify({'success': False, 'message': '请先保存完整的基础 URL 和密钥'}), 400

    preferred_model = (request.get_json(silent=True) or {}).get('model_name', '')
    discovery = discover_and_probe_models(
        api_base_url=provider.api_base_url,
        api_key=provider.api_key,
        preferred_model=preferred_model,
        interface_mode=provider.interface_mode or AI_INTERFACE_MODE_AUTO,
    )
    provider.set_model_catalog(discovery['catalog'])
    provider.set_recommended_models(discovery['selected_models'])
    provider.probe_last_at = _parse_probe_datetime(discovery.get('probe_last_at', ''))
    provider.probe_error = ''
    provider.probe_signature = discovery.get('probe_signature') or ''
    settings = SiteSettings.get_settings()
    _sync_provider_backfill(settings)
    db.session.commit()
    stats = discovery['stats']
    return jsonify({
        'success': True,
        'message': f"检测完成：发现 {stats['total_models']} 个模型，适配 {stats['compatible_models']} 个，部分兼容 {stats['partial_models']} 个。",
        'provider': provider.to_dict(include_catalog=True),
        'state': _build_ai_management_state(settings),
    })


@bp.route('/api/ai-providers/detect-all', methods=['POST'])
@login_required
@superadmin_required
def detect_all_ai_providers():
    settings = SiteSettings.get_settings()
    providers = settings.get_ai_providers(enabled_only=True)
    if not providers:
        return jsonify({'success': False, 'message': '没有可检测的已启用提供方。'}), 400

    detected = []
    errors = []
    for provider in providers:
        if not all([(provider.api_base_url or '').strip(), (provider.api_key or '').strip()]):
            errors.append(f'{provider.name}: 缺少基础 URL 或密钥')
            continue
        try:
            discovery = discover_and_probe_models(
                api_base_url=provider.api_base_url,
                api_key=provider.api_key,
                preferred_model='',
                interface_mode=provider.interface_mode or AI_INTERFACE_MODE_AUTO,
            )
            provider.set_model_catalog(discovery['catalog'])
            provider.set_recommended_models(discovery['selected_models'])
            provider.probe_last_at = _parse_probe_datetime(discovery.get('probe_last_at', ''))
            provider.probe_error = ''
            provider.probe_signature = discovery.get('probe_signature') or ''
            detected.append(provider.name)
        except Exception as exc:
            provider.probe_error = str(exc)
            errors.append(f'{provider.name}: {str(exc)}')

    _sync_provider_backfill(settings)
    db.session.commit()

    message_parts = []
    if detected:
        message_parts.append(f'已检测 {len(detected)} 个提供方')
    if errors:
        message_parts.append(f'失败 {len(errors)} 个')

    return jsonify({
        'success': bool(detected) and not errors,
        'message': '，'.join(message_parts) or '未执行任何检测',
        'detected': detected,
        'errors': errors,
        'state': _build_ai_management_state(settings),
    })


@bp.route('/api/ai-providers/<int:provider_id>/test', methods=['POST'])
@login_required
@superadmin_required
def test_ai_provider(provider_id: int):
    provider = AIProviderConfig.query.get_or_404(provider_id)
    data = request.get_json(silent=True) or {}
    model_name = (data.get('model_name') or '').strip() or _first_provider_model(provider)
    if not model_name:
        return jsonify({'success': False, 'message': '请先检测模型或手动指定要测试的模型'}), 400

    try:
        test_result = _test_provider_model(provider, model_name)
        ai_service = test_result['service']
        return jsonify({
            'success': True,
            'message': f'提供方测试成功：{provider.name} / {model_name}',
            'details': {
                'transport_protocol': test_result['transport_result'].get('protocol', ''),
                'structured_protocol': ai_service.last_protocol_used or '',
                'attempts': ai_service.last_attempt_trace,
            },
        })
    except (AIEmptyResponseError, AIJSONParseError, AICompatibilityError) as exc:
        return jsonify({'success': False, 'message': _format_ai_structured_output_error(exc)}), 400
    except Exception as exc:
        return jsonify({'success': False, 'message': f'测试失败: {str(exc)}'}), 400


@bp.route('/api/ai-task-bindings/save', methods=['POST'])
@login_required
@superadmin_required
def save_ai_task_bindings():
    settings = SiteSettings.get_settings()
    data = request.get_json(silent=True) or {}
    bindings = data.get('task_bindings')
    if not isinstance(bindings, dict):
        return jsonify({'success': False, 'message': '任务绑定数据格式错误'}), 400

    settings.set_ai_task_bindings(bindings)
    _sync_provider_backfill(settings)
    db.session.commit()
    return jsonify({
        'success': True,
        'message': '任务模型设置已保存',
        'state': _build_ai_management_state(settings),
    })


@bp.route('/api/test-ai-tasks', methods=['POST'])
@login_required
@superadmin_required
def test_ai_tasks():
    settings = SiteSettings.get_settings()
    data = request.get_json(silent=True) or {}
    posted_bindings = data.get('task_bindings')
    if isinstance(posted_bindings, dict):
        settings.set_ai_task_bindings(posted_bindings)

    results = {}
    for task in AI_TASK_KEYS:
        results[task] = _run_task_test(task, settings)

    settings.set_ai_task_test_results(results)
    _sync_provider_backfill(settings)
    db.session.commit()

    success_count = sum(1 for item in results.values() if item.get('status') == 'success')
    return jsonify({
        'success': success_count == len(AI_TASK_KEYS),
        'message': f'已完成 4 项测试，成功 {success_count} 项，失败 {len(AI_TASK_KEYS) - success_count} 项。',
        'results': results,
        'state': _build_ai_management_state(settings),
    })


@bp.route('/api/test-ai-config', methods=['POST'])
@login_required
@superadmin_required
def test_ai_config():
    data = request.get_json(silent=True) or {}
    settings = SiteSettings.get_settings()

    provider = AIProviderConfig(
        name='临时测试提供方',
        api_base_url=(data.get('api_base_url') or settings.ai_api_base_url or '').strip(),
        api_key=((settings.ai_api_key or '').strip() if not (data.get('api_key') or '').strip() or '*' in (data.get('api_key') or '') else (data.get('api_key') or '').strip()),
        interface_mode=(data.get('interface_mode') or settings.ai_interface_mode or AI_INTERFACE_MODE_AUTO).strip().lower(),
    )
    model_name = (data.get('model_name') or get_ai_model_for_task(settings, task='intent') or settings.ai_model_name or '').strip()
    if not all([(provider.api_base_url or '').strip(), (provider.api_key or '').strip(), model_name]):
        return jsonify({'success': False, 'message': '请填写完整的 API 基础 URL、API 密钥，并提供可测试的模型。'}), 400

    try:
        test_result = _test_provider_model(provider, model_name)
        ai_service = test_result['service']
        return jsonify({
            'success': True,
            'message': f'AI 配置测试成功，模型 {model_name} 可用于文本与结构化输出（{ai_service.last_protocol_used or provider.interface_mode}）。',
            'details': {
                'transport_ok': True,
                'structured_output_ok': True,
                'tested_model': model_name,
                'interface_mode': provider.interface_mode,
                'transport_protocol': test_result['transport_result'].get('protocol', ''),
                'structured_protocol': ai_service.last_protocol_used or '',
                'attempts': ai_service.last_attempt_trace,
            },
        })
    except (AIEmptyResponseError, AIJSONParseError, AICompatibilityError) as exc:
        return jsonify({'success': False, 'message': _format_ai_structured_output_error(exc)}), 400
    except Exception as exc:
        return jsonify({'success': False, 'message': f'测试失败: {str(exc)}'}), 400


@bp.route('/api/detect-ai-models', methods=['POST'])
@login_required
@superadmin_required
def detect_ai_models():
    data = request.get_json(silent=True) or {}
    settings = SiteSettings.get_settings()
    api_base_url = (data.get('api_base_url') or settings.ai_api_base_url or '').strip()
    api_key_input = (data.get('api_key') or '').strip()
    api_key = (settings.ai_api_key or '').strip() if not api_key_input or '*' in api_key_input else api_key_input
    model_name = (data.get('model_name') or settings.ai_model_name or '').strip()
    interface_mode = (data.get('interface_mode') or settings.ai_interface_mode or AI_INTERFACE_MODE_AUTO).strip().lower()

    if not all([api_base_url, api_key]):
        return jsonify({'success': False, 'message': '请先填写 API 基础 URL 和 API 密钥。'}), 400

    discovery = discover_and_probe_models(
        api_base_url=api_base_url,
        api_key=api_key,
        preferred_model=model_name,
        interface_mode=interface_mode,
    )
    probe_state = {
        'auto_enabled': bool(data.get('auto_enabled', True)),
        'manual_model_name': model_name,
        'interface_mode': interface_mode,
        'selected_models': discovery['selected_models'],
        'catalog': discovery['catalog'],
        'stats': discovery['stats'],
        'probe_last_at': discovery['probe_last_at'],
        'probe_error': '',
        'probe_signature': discovery['probe_signature'],
        'probe_stale': False,
    }
    stats = discovery['stats']
    return jsonify({
        'success': True,
        'message': f"模型检测完成：共发现 {stats['total_models']} 个模型，其中 {stats['compatible_models']} 个适合当前项目。",
        'probe_state': probe_state,
    })


@bp.route('/api/test-vector-config', methods=['POST'])
@login_required
@superadmin_required
def test_vector_config():
    try:
        data = request.get_json()
        embedding_api_base_url = data.get('embedding_api_base_url', '').strip()
        embedding_api_key_input = data.get('embedding_api_key', '').strip()
        embedding_model = data.get('embedding_model', '').strip()
        qdrant_url = data.get('qdrant_url', '').strip()

        settings = SiteSettings.get_settings()
        if not embedding_api_base_url:
            embedding_api_base_url = settings.embedding_api_base_url or ''
        if not embedding_api_key_input or '*' in embedding_api_key_input:
            embedding_api_key = settings.embedding_api_key or ''
        else:
            embedding_api_key = embedding_api_key_input

        if not embedding_api_base_url or not embedding_api_key:
            embedding_api_base_url = settings.get_embedding_api_config()[0] or ''
            embedding_api_key = settings.get_embedding_api_config()[1] or ''

        if not embedding_model:
            embedding_model = settings.embedding_model or 'text-embedding-3-small'
        if not qdrant_url:
            qdrant_url = settings.qdrant_url or SiteSettings.get_default_qdrant_url()

        if not all([embedding_api_base_url, embedding_api_key, embedding_model, qdrant_url]):
            return jsonify({'success': False, 'message': '请填写完整的向量搜索配置。'}), 400

        from app.utils.vector_service import EmbeddingClient
        embedding_client = EmbeddingClient(
            api_base_url=embedding_api_base_url,
            api_key=embedding_api_key,
            model_name=embedding_model,
        )

        embedding_success = False
        embedding_message = ''
        test_vector = None
        try:
            test_vector = embedding_client.generate_embedding('测试', use_cache=False)
            if test_vector and len(test_vector) > 0:
                embedding_success = True
                embedding_message = f'✅ Embedding API 连接成功（向量维度 {len(test_vector)}）'
            else:
                embedding_message = '❌ Embedding API 测试失败：未能生成向量'
        except Exception as embedding_error:
            from app.utils.api_error_handler import classify_api_error, format_error_for_display
            import requests

            response = None
            if isinstance(embedding_error, requests.exceptions.HTTPError) and hasattr(embedding_error, 'response'):
                response = embedding_error.response
            elif hasattr(embedding_error, 'response'):
                response = getattr(embedding_error, 'response', None)
            error_info = classify_api_error(
                exception=embedding_error,
                api_type='embedding',
                api_base_url=embedding_api_base_url,
                model_name=embedding_model,
                api_key=embedding_api_key,
                response=response,
            )
            embedding_message = f'❌ {format_error_for_display(error_info)}'

        from qdrant_client import QdrantClient
        qdrant_success = False
        qdrant_message = ''
        try:
            qdrant_client = QdrantClient(url=qdrant_url, timeout=5)
            qdrant_client.get_collections()
            qdrant_success = True
            qdrant_message = '✅ Qdrant 连接成功'
        except ConnectionError as conn_error:
            qdrant_message = f'❌ Qdrant 连接失败：{str(conn_error)[:100]}'
        except Exception as qdrant_error:
            error_str = str(qdrant_error)
            if '10061' in error_str or '拒绝' in error_str or 'refused' in error_str.lower() or 'Connection' in str(type(qdrant_error).__name__):
                qdrant_message = f'❌ Qdrant 连接失败：{error_str[:100]}'
            else:
                qdrant_success = True
                qdrant_message = f'✅ Qdrant 连接成功（注意：{error_str[:100]}）'

        overall_success = embedding_success and qdrant_success
        overall_message = '向量搜索配置测试成功' if overall_success else '向量搜索配置部分失败'
        return jsonify({
            'success': overall_success,
            'message': overall_message,
            'details': {
                'embedding_api': embedding_message,
                'qdrant': qdrant_message,
                'vector_dimension': len(test_vector) if test_vector else None,
            },
        })
    except Exception as exc:
        current_app.logger.error(f'向量搜索配置测试失败: {str(exc)}')
        return jsonify({'success': False, 'message': f'测试失败: {str(exc)}'}), 400
