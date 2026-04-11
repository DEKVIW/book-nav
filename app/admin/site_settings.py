#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""站点设置管理路由"""

from datetime import datetime
from typing import Optional

from flask import render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required
from app import db
from app.admin import bp
from app.admin.forms import SiteSettingsForm
from app.admin.decorators import superadmin_required
from app.admin.utils import save_image
from app.models import SiteSettings
from app.utils.ai_model_discovery import (
    compute_ai_probe_signature,
    discover_and_probe_models,
    summarize_probe_catalog,
)
from app.utils.ai_search import (
    AI_INTERFACE_MODE_AUTO,
    AISearchService,
    AICompatibilityError,
    AIEmptyResponseError,
    AIJSONParseError,
    get_ai_interface_mode,
    get_ai_model_for_task,
)


def _format_ai_structured_output_error(error: Exception) -> str:
    if isinstance(error, AIEmptyResponseError):
        reason = f"接口已连通，但模型返回了空内容或非直接文本内容：{str(error)}"
        suggestion = "请优先选择会直接输出文本内容的聊天模型，并关闭工具调用/仅推理模式；必要时可切换接口模式为自动兜底。"
    elif isinstance(error, AIJSONParseError):
        reason = f"接口已连通，但结构化 JSON 输出解析失败：{str(error)}"
        suggestion = "请使用能稳定输出 JSON 的聊天模型，或切换接口模式为自动兜底/Responses。"
    elif isinstance(error, AICompatibilityError):
        reason = f"接口已连通，但返回格式与当前项目不兼容：{str(error)}"
        suggestion = "请确保当前接口模式返回标准文本内容，并避免返回 tool_calls 作为最终结果。"
    else:
        reason = f"接口已连通，但结构化功能测试失败：{str(error)}"
        suggestion = "请检查当前模型是否适合稳定文本/JSON 输出。"
    return f"❌ AI 接口连通成功，但项目结构化功能测试失败\n原因：{reason}\n建议：{suggestion}"


def _resolve_ai_request_config(data: dict, settings: SiteSettings) -> tuple[str, str, str, str]:
    api_base_url = (data.get('api_base_url') or '').strip()
    api_key_input = (data.get('api_key') or '').strip()
    model_name = (data.get('model_name') or '').strip()
    interface_mode = (data.get('interface_mode') or '').strip().lower()

    if not api_base_url:
        api_base_url = (settings.ai_api_base_url or '').strip()

    if not api_key_input or '*' in api_key_input:
        api_key = (settings.ai_api_key or '').strip()
    else:
        api_key = api_key_input

    if not model_name:
        model_name = (
            get_ai_model_for_task(settings, task='intent')
            or (settings.ai_model_name or '').strip()
        )

    if not interface_mode:
        interface_mode = get_ai_interface_mode(settings)
    elif interface_mode not in {'auto', 'chat', 'responses'}:
        interface_mode = AI_INTERFACE_MODE_AUTO

    return api_base_url, api_key, model_name, interface_mode


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


def _clear_ai_probe_results(settings: SiteSettings) -> None:
    settings.ai_model_catalog_json = None
    settings.ai_selected_intent_model = None
    settings.ai_selected_rerank_model = None
    settings.ai_selected_translate_model = None
    settings.ai_selected_site_info_model = None
    settings.ai_selected_fallback_model = None
    settings.ai_model_probe_last_at = None
    settings.ai_model_probe_signature = None


def _build_ai_probe_state(settings: SiteSettings) -> dict:
    catalog = settings.get_ai_model_catalog()
    current_signature = compute_ai_probe_signature(
        settings.ai_api_base_url or '',
        settings.ai_api_key or '',
        get_ai_interface_mode(settings),
    )
    stored_signature = (settings.ai_model_probe_signature or '').strip()
    probe_stale = bool(
        catalog
        and current_signature
        and stored_signature
        and stored_signature != current_signature
    )
    if catalog and current_signature and not stored_signature:
        probe_stale = True

    return {
        'auto_enabled': bool(getattr(settings, 'ai_auto_model_selection_enabled', True)),
        'manual_model_name': (settings.ai_model_name or '').strip(),
        'interface_mode': get_ai_interface_mode(settings),
        'selected_models': {
            key: (value or '')
            for key, value in settings.get_ai_selected_models().items()
        },
        'catalog': catalog,
        'stats': summarize_probe_catalog(catalog),
        'probe_last_at': settings.ai_model_probe_last_at.isoformat() if settings.ai_model_probe_last_at else '',
        'probe_error': settings.ai_model_probe_error or '',
        'probe_signature': stored_signature,
        'probe_stale': probe_stale,
    }


@bp.route('/site-settings', methods=['GET', 'POST'])
@login_required
@superadmin_required
def site_settings():
    try:
        settings = SiteSettings.get_settings()
        form = SiteSettingsForm(obj=settings)
        
        # 如果已配置API密钥，在表单中显示掩码（GET请求时）
        if request.method == 'GET' and settings.ai_api_key:
            # 显示前4位和后4位，中间用*代替
            if len(settings.ai_api_key) > 8:
                masked_key = settings.ai_api_key[:4] + '*' * (len(settings.ai_api_key) - 8) + settings.ai_api_key[-4:]
            else:
                masked_key = '*' * len(settings.ai_api_key)
            # 设置表单字段的值
            form.ai_api_key.data = masked_key
            # 同时设置对象的原始值，确保表单能正确显示
            form.ai_api_key.raw_data = [masked_key]
        
        # 如果已配置 Embedding API 密钥，在表单中显示掩码（GET请求时）
        if request.method == 'GET' and settings.embedding_api_key:
            # 显示前4位和后4位，中间用*代替
            if len(settings.embedding_api_key) > 8:
                masked_key = settings.embedding_api_key[:4] + '*' * (len(settings.embedding_api_key) - 8) + settings.embedding_api_key[-4:]
            else:
                masked_key = '*' * len(settings.embedding_api_key)
            # 设置表单字段的值
            form.embedding_api_key.data = masked_key
            # 同时设置对象的原始值，确保表单能正确显示
            form.embedding_api_key.raw_data = [masked_key]
        
        # 如果Qdrant URL为空，自动填充默认值（根据Docker环境）
        if request.method == 'GET' and not settings.qdrant_url:
            form.qdrant_url.data = SiteSettings.get_default_qdrant_url()
        
        # 输出初始设置值
        current_app.logger.info(f"初始设置: enable_transition={settings.enable_transition}, theme={settings.transition_theme}")
        
        if form.validate_on_submit():
            # 输出表单提交的值
            current_app.logger.info(f"表单数据: enable_transition={form.enable_transition.data}, theme={form.transition_theme.data}")
            
            # 处理Logo上传
            if form.logo_file.data:
                logo_filename = save_image(form.logo_file.data, 'logos')
                if logo_filename:
                    settings.site_logo = url_for('static', filename=f'uploads/logos/{logo_filename}')
            elif form.site_logo.data:
                settings.site_logo = form.site_logo.data
            elif not form.site_logo.data and 'clear_logo' in request.form:
                # 清空Logo
                settings.site_logo = None
                
            # 处理Favicon上传
            if form.favicon_file.data:
                favicon_filename = save_image(form.favicon_file.data, 'favicons')
                if favicon_filename:
                    settings.site_favicon = url_for('static', filename=f'uploads/favicons/{favicon_filename}')
            elif form.site_favicon.data:
                settings.site_favicon = form.site_favicon.data
            elif not form.site_favicon.data and 'clear_favicon' in request.form:
                # 清空Favicon
                settings.site_favicon = None
                
            # 处理背景上传
            if form.background_file.data and form.background_type.data == 'image':
                bg_filename = save_image(form.background_file.data, 'backgrounds')
                if bg_filename:
                    settings.background_url = url_for('static', filename=f'uploads/backgrounds/{bg_filename}')
            elif form.background_url.data:
                settings.background_url = form.background_url.data
                
            # 更新其他字段
            settings.site_name = form.site_name.data
            settings.site_subtitle = form.site_subtitle.data
            settings.site_keywords = form.site_keywords.data
            settings.site_description = form.site_description.data
            settings.footer_content = form.footer_content.data
            settings.background_type = form.background_type.data
            
            # 更新PC端背景设置
            if form.pc_background_file.data and form.pc_background_type.data == 'image':
                pc_bg_filename = save_image(form.pc_background_file.data, 'backgrounds')
                if pc_bg_filename:
                    settings.pc_background_url = url_for('static', filename=f'uploads/backgrounds/{pc_bg_filename}')
            elif form.pc_background_url.data:
                settings.pc_background_url = form.pc_background_url.data
            settings.pc_background_type = form.pc_background_type.data
            
            # 更新移动端背景设置
            if form.mobile_background_file.data and form.mobile_background_type.data == 'image':
                mobile_bg_filename = save_image(form.mobile_background_file.data, 'backgrounds')
                if mobile_bg_filename:
                    settings.mobile_background_url = url_for('static', filename=f'uploads/backgrounds/{mobile_bg_filename}')
            elif form.mobile_background_url.data:
                settings.mobile_background_url = form.mobile_background_url.data
            settings.mobile_background_type = form.mobile_background_type.data
            
            # 更新过渡页设置
            settings.enable_transition = form.enable_transition.data
            settings.transition_time = form.transition_time.data
            settings.admin_transition_time = form.admin_transition_time.data
            settings.transition_ad1 = form.transition_ad1.data
            settings.transition_ad2 = form.transition_ad2.data
            settings.transition_remember_choice = form.transition_remember_choice.data
            settings.transition_show_description = form.transition_show_description.data
            settings.transition_theme = form.transition_theme.data
            settings.transition_color = form.transition_color.data

            # 更新公告设置
            settings.announcement_enabled = form.announcement_enabled.data
            settings.announcement_title = form.announcement_title.data
            # 直接保存原始HTML内容，不做bleach过滤
            content = form.announcement_content.data
            settings.announcement_content = content
            settings.announcement_start = form.announcement_start.data
            settings.announcement_end = form.announcement_end.data
            settings.announcement_remember_days = form.announcement_remember_days.data
            
            # 更新AI搜索设置
            settings.ai_search_enabled = form.ai_search_enabled.data
            settings.ai_search_allow_anonymous = form.ai_search_allow_anonymous.data
            settings.ai_api_base_url = form.ai_api_base_url.data
            # 只有提供了新密钥才更新（留空则不修改，如果输入的是掩码则保持原值）
            api_key_input = form.ai_api_key.data.strip() if form.ai_api_key.data else ""
            # 判断是否为掩码：包含*号且长度较短（掩码通常是前4位+*号+后4位，或全是*号）
            is_masked = api_key_input and ('*' in api_key_input or (len(api_key_input) < 20 and api_key_input.count('*') > 0))
            if api_key_input and not is_masked:
                settings.ai_api_key = api_key_input
            settings.ai_model_name = form.ai_model_name.data
            settings.ai_interface_mode = (form.ai_interface_mode.data or AI_INTERFACE_MODE_AUTO).strip().lower()
            settings.ai_auto_model_selection_enabled = bool(request.form.get('ai_auto_model_selection_enabled'))
            try:
                settings.ai_temperature = float(form.ai_temperature.data) if form.ai_temperature.data else 0.7
            except (ValueError, TypeError):
                settings.ai_temperature = 0.7
            settings.ai_max_tokens = form.ai_max_tokens.data if form.ai_max_tokens.data else 500

            probe_signature = compute_ai_probe_signature(
                settings.ai_api_base_url or '',
                settings.ai_api_key or '',
                get_ai_interface_mode(settings),
            )
            submitted_probe_signature = (request.form.get('ai_model_probe_signature') or '').strip()
            submitted_probe_error = (request.form.get('ai_model_probe_error') or '').strip()
            submitted_catalog_json = (request.form.get('ai_model_catalog_json') or '').strip()

            if not probe_signature:
                _clear_ai_probe_results(settings)
                settings.ai_model_probe_error = None
            elif submitted_probe_signature and submitted_probe_signature == probe_signature:
                settings.ai_model_catalog_json = submitted_catalog_json or None
                settings.ai_selected_intent_model = (request.form.get('ai_selected_intent_model') or '').strip() or None
                settings.ai_selected_rerank_model = (request.form.get('ai_selected_rerank_model') or '').strip() or None
                settings.ai_selected_translate_model = (request.form.get('ai_selected_translate_model') or '').strip() or None
                settings.ai_selected_site_info_model = (request.form.get('ai_selected_site_info_model') or '').strip() or None
                settings.ai_selected_fallback_model = (request.form.get('ai_selected_fallback_model') or '').strip() or None
                settings.ai_model_probe_last_at = _parse_probe_datetime(
                    request.form.get('ai_model_probe_last_at', '')
                )
                settings.ai_model_probe_error = submitted_probe_error or None
                settings.ai_model_probe_signature = submitted_probe_signature
            elif settings.ai_model_probe_signature and settings.ai_model_probe_signature != probe_signature:
                _clear_ai_probe_results(settings)
                settings.ai_model_probe_error = 'AI API 配置已变更，请重新检测模型'

            # 更新向量搜索设置
            settings.vector_search_enabled = form.vector_search_enabled.data
            # 更新 Embedding API 配置（独立配置）
            settings.embedding_api_base_url = form.embedding_api_base_url.data if form.embedding_api_base_url.data else None
            # 处理 Embedding API 密钥（类似 AI API 密钥的处理逻辑）
            embedding_api_key_input = form.embedding_api_key.data.strip() if form.embedding_api_key.data else ""
            # 判断是否为掩码：包含*号且长度较短
            is_masked = embedding_api_key_input and ('*' in embedding_api_key_input or (len(embedding_api_key_input) < 20 and embedding_api_key_input.count('*') > 0))
            if embedding_api_key_input and not is_masked:
                settings.embedding_api_key = embedding_api_key_input
            # 去除 Qdrant URL 末尾的斜杠（如果存在）
            if form.qdrant_url.data:
                qdrant_url = form.qdrant_url.data.strip().rstrip('/')
            else:
                # 如果未填写，使用默认值（自动检测Docker环境）
                qdrant_url = SiteSettings.get_default_qdrant_url()
            settings.qdrant_url = qdrant_url
            settings.embedding_model = form.embedding_model.data if form.embedding_model.data else 'text-embedding-3-small'
            try:
                settings.vector_similarity_threshold = float(form.vector_similarity_threshold.data) if form.vector_similarity_threshold.data else 0.3
            except (ValueError, TypeError):
                settings.vector_similarity_threshold = 0.3
            settings.vector_max_results = form.vector_max_results.data if form.vector_max_results.data else 50
            
            # 检查AI配置是否完整
            ai_configured = bool(
                settings.ai_api_base_url
                and settings.ai_api_key
                and get_ai_model_for_task(settings)
            )
            
            # 检查向量搜索配置是否完整（使用 get_embedding_api_config 方法，支持向后兼容）
            embedding_api_url, embedding_api_key = settings.get_embedding_api_config()
            vector_configured = all([
                settings.vector_search_enabled,
                settings.qdrant_url,
                settings.embedding_model,
                embedding_api_url,
                embedding_api_key
            ])

            # 确认设置值已更新
            current_app.logger.info(f"更新后的设置: enable_transition={settings.enable_transition}, theme={settings.transition_theme}")
            
            try:
                db.session.commit()
                # 验证保存后的值
                db.session.refresh(settings)
                current_app.logger.info(f"保存后的设置: enable_transition={settings.enable_transition}, theme={settings.transition_theme}")
                
                # 根据配置状态显示不同的提示
                if vector_configured:
                    flash('站点设置已更新，向量搜索已配置完成', 'success')
                elif ai_configured and settings.ai_search_enabled:
                    flash('站点设置已更新，AI搜索已配置并启用', 'success')
                elif ai_configured:
                    flash('站点设置已更新，AI搜索已配置（未启用）', 'success')
                else:
                    flash('站点设置已更新', 'success')
                return redirect(url_for('admin.site_settings'))
            except Exception as e:
                db.session.rollback()
                flash(f'保存设置失败: {str(e)}', 'danger')
                current_app.logger.error(f"保存站点设置失败: {str(e)}")
                
        return render_template(
            'admin/site_settings.html',
            title='站点设置',
            form=form,
            settings=settings,
            ai_probe_state=_build_ai_probe_state(settings),
        )
    except Exception as e:
        flash(f'加载站点设置失败: {str(e)}', 'danger')
        current_app.logger.error(f"加载站点设置失败: {str(e)}")
        return redirect(url_for('admin.index'))


@bp.route('/api/test-ai-config', methods=['POST'])
@login_required
@superadmin_required
def test_ai_config():
    """测试AI配置是否有效"""
    try:
        data = request.get_json(silent=True) or {}
        settings = SiteSettings.get_settings()
        api_base_url, api_key, model_name, interface_mode = _resolve_ai_request_config(data, settings)
        
        if not all([api_base_url, api_key, model_name]):
            return jsonify({
                'success': False,
                'message': '请填写完整的 API 基础URL、API 密钥，或先完成模型检测'
            }), 400

        from app.utils.api_error_handler import classify_api_error, format_error_for_display

        try:
            transport_result = None
            ai_service = AISearchService(
                api_base_url=api_base_url,
                api_key=api_key,
                model_name=model_name,
                interface_mode=interface_mode,
            )

            transport_result = ai_service.probe_text_output()
            test_result = ai_service.analyze_search_intent("测试")
            
            return jsonify({
                'success': True,
                'message': (
                    f'AI配置测试成功，模型 {model_name} 接口连通且结构化输出正常'
                    f'（当前使用 {ai_service.last_protocol_used or interface_mode}）'
                ),
                'result': test_result,
                'details': {
                    'transport_ok': True,
                    'structured_output_ok': True,
                    'tested_model': model_name,
                    'interface_mode': interface_mode,
                    'transport_protocol': transport_result.get('protocol', ''),
                    'structured_protocol': ai_service.last_protocol_used or '',
                    'attempts': ai_service.last_attempt_trace,
                }
            })
        except (AIEmptyResponseError, AIJSONParseError, AICompatibilityError) as e:
            current_app.logger.error(f"AI结构化功能测试失败: {str(e)}")
            return jsonify({
                'success': False,
                'message': _format_ai_structured_output_error(e),
                'details': {
                    'transport_ok': bool(transport_result),
                    'structured_output_ok': False,
                    'error_type': type(e).__name__,
                    'interface_mode': interface_mode,
                    'attempts': getattr(locals().get('ai_service'), 'last_attempt_trace', []),
                }
            }), 400
        except Exception as e:
            current_app.logger.error(f"AI服务调用失败: {str(e)}")
            
            error_info = classify_api_error(
                exception=e,
                api_type='ai',
                api_base_url=api_base_url,
                model_name=model_name,
                api_key=api_key,
                response=None
            )
            
            return jsonify({
                'success': False,
                'message': format_error_for_display(error_info)
            }), 400
        
    except Exception as e:
        # 外层异常捕获（处理配置获取等错误）
        current_app.logger.error(f"AI配置测试失败: {str(e)}")
        
        from app.utils.api_error_handler import classify_api_error, format_error_for_display
        
        error_info = classify_api_error(
            exception=e,
            api_type='ai',
            api_base_url=api_base_url if 'api_base_url' in locals() else '',
            model_name=model_name if 'model_name' in locals() else '',
            api_key=api_key if 'api_key' in locals() else '',
            response=None
        )
        
        return jsonify({
            'success': False,
            'message': format_error_for_display(error_info)
        }), 400


@bp.route('/api/detect-ai-models', methods=['POST'])
@login_required
@superadmin_required
def detect_ai_models():
    """探测当前 API Key 下可用的模型，并为项目自动推荐任务模型。"""
    try:
        data = request.get_json(silent=True) or {}
        settings = SiteSettings.get_settings()
        api_base_url, api_key, model_name, interface_mode = _resolve_ai_request_config(data, settings)

        if not all([api_base_url, api_key]):
            return jsonify({
                'success': False,
                'message': '请先填写 API 基础URL 和 API 密钥，或确保已保存配置'
            }), 400

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
            'message': (
                f"模型检测完成：共发现 {stats['total_models']} 个模型，"
                f"其中 {stats['compatible_models']} 个适合当前项目，"
                f"{stats['partial_models']} 个可部分兼容。"
                f" 当前接口模式：{interface_mode}。"
            ),
            'probe_state': probe_state
        })
    except Exception as e:
        current_app.logger.error(f"AI模型检测失败: {str(e)}")

        from app.utils.api_error_handler import classify_api_error, format_error_for_display

        error_info = classify_api_error(
            exception=e,
            api_type='ai',
            api_base_url=api_base_url if 'api_base_url' in locals() else '',
            model_name=model_name if 'model_name' in locals() else '',
            api_key=api_key if 'api_key' in locals() else '',
            response=getattr(e, 'response', None)
        )

        return jsonify({
            'success': False,
            'message': format_error_for_display(error_info)
        }), 400


@bp.route('/api/test-vector-config', methods=['POST'])
@login_required
@superadmin_required
def test_vector_config():
    """测试向量搜索配置（Embedding API + Qdrant）是否有效"""
    try:
        data = request.get_json()
        embedding_api_base_url = data.get('embedding_api_base_url', '').strip()
        embedding_api_key_input = data.get('embedding_api_key', '').strip()
        embedding_model = data.get('embedding_model', '').strip()
        qdrant_url = data.get('qdrant_url', '').strip()
        
        # 获取数据库中的真实配置
        settings = SiteSettings.get_settings()
        
        # 如果输入框为空或包含*号（掩码），使用数据库中的真实配置
        if not embedding_api_base_url:
            embedding_api_base_url = settings.embedding_api_base_url or ''
        if not embedding_api_key_input or '*' in embedding_api_key_input:
            embedding_api_key = settings.embedding_api_key or ''
        else:
            # 如果输入了新key，使用输入的key
            embedding_api_key = embedding_api_key_input
        
        # 如果未配置独立的 Embedding API，使用 AI 搜索配置（向后兼容）
        if not embedding_api_base_url or not embedding_api_key:
            embedding_api_base_url = settings.ai_api_base_url or ''
            embedding_api_key = settings.ai_api_key or ''
        
        # 如果模型名为空，使用数据库中的值
        if not embedding_model:
            embedding_model = settings.embedding_model or 'text-embedding-3-small'
        
        # 如果 Qdrant URL 为空，使用数据库中的值
        if not qdrant_url:
            qdrant_url = settings.qdrant_url or SiteSettings.get_default_qdrant_url()
        
        if not all([embedding_api_base_url, embedding_api_key, embedding_model, qdrant_url]):
            return jsonify({
                'success': False,
                'message': '请填写完整的向量搜索配置信息，或确保已保存配置'
            }), 400
        
        # 测试 Embedding API
        from app.utils.vector_service import EmbeddingClient
        embedding_client = EmbeddingClient(
            api_base_url=embedding_api_base_url,
            api_key=embedding_api_key,
            model_name=embedding_model
        )
        
        # 测试生成向量
        embedding_success = False
        embedding_message = ""
        test_vector = None
        try:
            test_vector = embedding_client.generate_embedding("测试", use_cache=False)
            if test_vector and len(test_vector) > 0:
                embedding_success = True
                embedding_message = f'✅ Embedding API 连接成功（向量维度: {len(test_vector)}）'
            else:
                embedding_message = '❌ Embedding API 测试失败：未能生成向量'
        except Exception as embedding_error:
            # 使用错误处理工具类解析错误
            from app.utils.api_error_handler import classify_api_error, format_error_for_display
            import requests
            
            # 尝试从异常中获取response对象
            response = None
            if isinstance(embedding_error, requests.exceptions.HTTPError) and hasattr(embedding_error, 'response'):
                response = embedding_error.response
            elif hasattr(embedding_error, 'response'):
                response = getattr(embedding_error, 'response', None)
            
            # 分类错误并返回友好提示
            error_info = classify_api_error(
                exception=embedding_error,
                api_type='embedding',
                api_base_url=embedding_api_base_url,
                model_name=embedding_model,
                api_key=embedding_api_key,
                response=response
            )
            embedding_message = f'❌ {format_error_for_display(error_info)}'
        
        # 测试 Qdrant 连接（独立测试，不影响 Embedding API 结果）
        from qdrant_client import QdrantClient
        qdrant_success = False
        qdrant_message = ""
        try:
            qdrant_client = QdrantClient(url=qdrant_url, timeout=5)
            # 尝试获取集合信息（如果集合不存在会抛出异常，但连接成功）
            collections = qdrant_client.get_collections()
            qdrant_success = True
            qdrant_message = "✅ Qdrant 连接成功"
        except ConnectionError as conn_error:
            # 连接失败
            error_msg = str(conn_error)[:100]
            qdrant_message = f'❌ Qdrant 连接失败：{error_msg}'
        except Exception as qdrant_error:
            # 连接成功但可能有其他问题（如集合不存在），检查是否是连接相关错误
            error_str = str(qdrant_error)
            if '10061' in error_str or '拒绝' in error_str or 'refused' in error_str.lower() or 'Connection' in str(type(qdrant_error).__name__):
                # 这是连接失败，不是成功
                qdrant_message = f'❌ Qdrant 连接失败：{error_str[:100]}'
            else:
                # 连接成功但可能有其他问题（如集合不存在）
                qdrant_success = True
                qdrant_message = f"✅ Qdrant 连接成功（注意：{error_str[:100]}）"
        
        # 根据两个测试的结果决定整体成功状态
        overall_success = embedding_success and qdrant_success
        overall_message = '向量搜索配置测试' + ('成功！' if overall_success else '部分失败')
        
        return jsonify({
            'success': overall_success,
            'message': overall_message,
            'details': {
                'embedding_api': embedding_message,
                'qdrant': qdrant_message,
                'vector_dimension': len(test_vector) if test_vector else None
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"向量搜索配置测试失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'测试失败: {str(e)}'
        }), 400

