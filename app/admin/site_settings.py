#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""站点设置管理路由"""

from flask import render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required
from app import db
from app.admin import bp
from app.admin.forms import SiteSettingsForm
from app.admin.decorators import superadmin_required
from app.admin.utils import save_image
from app.models import SiteSettings
from app.utils.ai_search import AISearchService


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
            try:
                settings.ai_temperature = float(form.ai_temperature.data) if form.ai_temperature.data else 0.7
            except (ValueError, TypeError):
                settings.ai_temperature = 0.7
            settings.ai_max_tokens = form.ai_max_tokens.data if form.ai_max_tokens.data else 500
            
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
            ai_configured = all([
                settings.ai_api_base_url,
                settings.ai_api_key,
                settings.ai_model_name
            ])
            
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
                
        return render_template('admin/site_settings.html', title='站点设置', form=form, settings=settings)
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
        data = request.get_json()
        api_base_url = data.get('api_base_url', '').strip()
        api_key_input = data.get('api_key', '').strip()
        model_name = data.get('model_name', '').strip()
        
        # 获取数据库中的真实配置
        settings = SiteSettings.get_settings()
        
        # 如果输入框为空或包含*号（掩码），使用数据库中的真实key
        if not api_key_input or '*' in api_key_input:
            api_key = settings.ai_api_key or ''
        else:
            # 如果输入了新key，使用输入的key
            api_key = api_key_input
        
        # 如果URL或模型名为空，使用数据库中的值
        if not api_base_url:
            api_base_url = settings.ai_api_base_url or ''
        if not model_name:
            model_name = settings.ai_model_name or ''
        
        if not all([api_base_url, api_key, model_name]):
            return jsonify({
                'success': False,
                'message': '请填写完整的API配置信息，或确保已保存配置'
            }), 400
        
        # 先进行简单的API连接测试，获取详细的错误信息
        import requests
        from app.utils.api_error_handler import classify_api_error, format_error_for_display
        
        test_url = f"{api_base_url.rstrip('/')}/v1/chat/completions"
        test_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        test_data = {
            "model": model_name,
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 5
        }
        
        # 执行测试请求以获取详细错误信息
        try:
            test_response = requests.post(test_url, json=test_data, headers=test_headers, timeout=10)
            test_response.raise_for_status()
        except requests.exceptions.RequestException as test_error:
            # 测试请求失败，使用错误处理工具类解析详细错误
            response = None
            if hasattr(test_error, 'response') and test_error.response is not None:
                response = test_error.response
            
            error_info = classify_api_error(
                exception=test_error,
                api_type='ai',
                api_base_url=api_base_url,
                model_name=model_name,
                api_key=api_key,
                response=response
            )
            
            current_app.logger.error(f"AI配置测试失败: {str(test_error)}")
            return jsonify({
                'success': False,
                'message': format_error_for_display(error_info)
            }), 400
        
        # 测试请求成功，继续使用AISearchService进行完整测试（验证功能完整性）
        try:
            ai_service = AISearchService(
                api_base_url=api_base_url,
                api_key=api_key,
                model_name=model_name
            )
            
            # 执行简单的测试查询
            test_result = ai_service.analyze_search_intent("测试")
            
            return jsonify({
                'success': True,
                'message': 'AI配置测试成功！',
                'result': test_result
            })
        except Exception as e:
            # AISearchService调用失败（这种情况应该很少，因为前面测试已通过）
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

