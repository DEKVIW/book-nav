#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""网站CRUD API路由"""

from flask import request, jsonify, current_app
from flask_login import current_user, login_required
from app import db, csrf
from app.main import bp
from app.models import Website, Category, OperationLog, SiteSettings
import json
import threading


@bp.route('/api/website/<int:site_id>/update', methods=['POST'])
@login_required
def update_website(site_id):
    """更新网站信息"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "message": "未提供数据"}), 400
        
        site = Website.query.get_or_404(site_id)
        
        if not current_user.is_admin:
            return jsonify({"success": False, "message": "没有权限执行此操作"}), 403
        
        if 'title' in data:
            site.title = data['title']
        if 'url' in data:
            site.url = data['url']
        if 'icon' in data:
            site.icon = data['icon']
        if 'description' in data:
            site.description = data['description']
        if 'is_private' in data:
            site.is_private = bool(data['is_private'])
        # 记录修改前的值（用于判断是否需要更新向量）
        old_title = site.title
        old_description = site.description
        old_category_id = site.category_id
        
        if 'category_id' in data and isinstance(data['category_id'], int):
            category = Category.query.get(data['category_id'])
            if category:
                site.category_id = data['category_id']
        
        db.session.commit()
        
        # 检查是否需要更新向量（标题、描述或分类变化时）
        needs_vector_update = (
            ('title' in data and data['title'] != old_title) or
            ('description' in data and data.get('description') != old_description) or
            ('category_id' in data and data.get('category_id') != old_category_id)
        )
        
        if needs_vector_update:
            try:
                category = Category.query.get(site.category_id) if site.category_id else None
                category_name = category.name if category else None
                _trigger_vector_indexing(site.id, category_name)
            except Exception as e:
                current_app.logger.warning(f"触发向量更新失败: {str(e)}")
        
        return jsonify({
            "success": True, 
            "message": "网站信息已成功更新",
            "website": {
                "id": site.id,
                "title": site.title,
                "url": site.url,
                "icon": site.icon,
                "description": site.description,
                "category_id": site.category_id,
                "is_private": site.is_private
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"更新失败: {str(e)}"}), 500


@bp.route('/api/website/update/<int:id>', methods=['POST'])
@login_required
def api_update_website(id):
    """更新网站链接的API接口"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    website = Website.query.get_or_404(id)
    
    data = request.get_json()
    if not data or 'url' not in data or not data['url']:
        return jsonify({'success': False, 'message': '无效的请求数据'}), 400
    
    url = data.get('url')
    if not url or not url.startswith(('http://', 'https://')):
        return jsonify({'success': False, 'message': 'URL格式不正确'}), 400
    
    old_title = website.title
    old_url = website.url
    old_description = website.description
    old_category_id = website.category_id
    old_category_name = website.category.name if website.category else None
    old_is_private = website.is_private
    old_is_featured = website.is_featured
    old_sort_order = website.sort_order
    
    website.url = url
    
    if 'title' in data:
        website.title = data['title']
    if 'description' in data:
        website.description = data['description']
    if 'icon' in data:
        website.icon = data['icon']
    if 'is_featured' in data and isinstance(data['is_featured'], bool):
        website.is_featured = data['is_featured']
    if 'category_id' in data and isinstance(data['category_id'], int):
        category = Category.query.get(data['category_id'])
        if category:
            website.category_id = data['category_id']
    if 'is_private' in data:
        website.is_private = bool(data['is_private'])
    if 'sort_order' in data:
        website.sort_order = int(data['sort_order'])
    
    db.session.commit()
    
    changes = {}
    if old_title != website.title:
        changes['title'] = {'old': old_title, 'new': website.title}
    if old_url != website.url:
        changes['url'] = {'old': old_url, 'new': website.url}
    if old_description != website.description:
        changes['description'] = {'old': old_description, 'new': website.description}
    if old_sort_order != website.sort_order:
        changes['sort_order'] = {'old': old_sort_order, 'new': website.sort_order}
        
    if old_category_id != website.category_id:
        new_category_name = website.category.name if website.category else None
        changes['category'] = {
            'old': old_category_name, 
            'new': new_category_name
        }
        
    if old_is_private != website.is_private:
        changes['is_private'] = {'old': old_is_private, 'new': website.is_private}
        
    if old_is_featured != website.is_featured:
        changes['is_featured'] = {'old': old_is_featured, 'new': website.is_featured}
    
    if changes:
        operation_log = OperationLog(
            user_id=current_user.id,
            operation_type='MODIFY',
            website_id=website.id,
            website_title=website.title,
            website_url=website.url,
            website_icon=website.icon,
            category_id=website.category_id,
            category_name=website.category.name if website.category else None,
            details=json.dumps(changes)
        )
        db.session.add(operation_log)
        db.session.commit()
    
    # 检查是否需要更新向量（标题、描述或分类变化时）
    needs_vector_update = (
        old_title != website.title or
        old_description != website.description or
        old_category_id != website.category_id
    )
    
    if needs_vector_update:
        try:
            new_category_name = website.category.name if website.category else None
            _trigger_vector_indexing(website.id, new_category_name)
        except Exception as e:
            current_app.logger.warning(f"触发向量更新失败: {str(e)}")
    
    return jsonify({
        'success': True, 
        'message': '网站信息更新成功',
        'website': {
            'id': website.id,
            'title': website.title,
            'url': website.url,
            'description': website.description,
            'icon': website.icon,
            'is_featured': website.is_featured,
            'category_id': website.category_id,
            'sort_order': website.sort_order
        }
    })


@bp.route('/api/website/delete/<int:id>', methods=['POST', 'DELETE'])
@login_required
def api_delete_website(id):
    """删除网站链接的API接口"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    try:
        website = Website.query.get_or_404(id)
        
        website_title = website.title
        
        details = {
            'description': website.description,
            'is_private': website.is_private,
            'is_featured': website.is_featured
        }
        
        operation_log = OperationLog(
            user_id=current_user.id,
            operation_type='DELETE',
            website_id=None,
            website_title=website.title,
            website_url=website.url,
            website_icon=website.icon,
            category_id=website.category_id,
            category_name=website.category.name if website.category else None,
            details=json.dumps(details)
        )
        
        db.session.add(operation_log)
        
        db.session.delete(website)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'网站"{website_title}"已成功删除'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


@bp.route('/api/modify_link', methods=['POST'])
@login_required
def api_modify_link():
    """修改链接的API接口"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    data = request.get_json()
    if not data or 'url' not in data or not data['url']:
        return jsonify({'success': False, 'message': '无效的请求数据'}), 400
    
    website_id = data.get('id')
    if not website_id:
        return jsonify({'success': False, 'message': '未提供网站ID'}), 400
    
    try:
        website = Website.query.get_or_404(website_id)
        
        old_title = website.title
        old_url = website.url
        old_description = website.description
        
        website.url = data['url']
        
        if 'title' in data and data['title']:
            website.title = data['title']
        if 'description' in data and data['description']:
            website.description = data['description']
        if 'icon' in data and data['icon']:
            website.icon = data['icon']
        
        db.session.commit()
        
        changes = {}
        if old_title != website.title:
            changes['title'] = {'old': old_title, 'new': website.title}
        if old_url != website.url:
            changes['url'] = {'old': old_url, 'new': website.url}
        if old_description != website.description:
            changes['description'] = {'old': old_description, 'new': website.description}
        
        if changes:
            operation_log = OperationLog(
                user_id=current_user.id,
                operation_type='MODIFY',
                website_id=website.id,
                website_title=website.title,
                website_url=website.url,
                website_icon=website.icon,
                category_id=website.category_id,
                category_name=website.category.name if website.category else None,
                details=json.dumps(changes)
            )
            db.session.add(operation_log)
            db.session.commit()
        
        # 检查是否需要更新向量（标题、描述变化时）
        needs_vector_update = (
            old_title != website.title or
            old_description != website.description
        )
        
        if needs_vector_update:
            try:
                category_name = website.category.name if website.category else None
                _trigger_vector_indexing(website.id, category_name)
            except Exception as e:
                current_app.logger.warning(f"触发向量更新失败: {str(e)}")
        
        return jsonify({'success': True, 'message': '链接已更新'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'修改失败: {str(e)}'}), 500


@bp.route('/api/website/update_order', methods=['POST'])
@login_required
@csrf.exempt
def update_website_order():
    """更新网站排序顺序的API接口"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    data = request.get_json()
    if not data or 'items' not in data:
        return jsonify({'success': False, 'message': '无效的请求数据'}), 400
    
    items = data['items']
    category_id = data.get('category_id')
    
    try:
        if category_id:
            website_ids = [item.get('id') for item in items if item.get('id')]
            websites_in_category = Website.query.filter(
                Website.id.in_(website_ids),
                Website.category_id == category_id
            ).count()
            
            if websites_in_category != len(website_ids):
                return jsonify({'success': False, 'message': '部分网站不属于指定分类'}), 400
        
        all_websites_query = Website.query.filter_by(category_id=category_id)
        
        if not current_user.is_authenticated:
            all_websites_query = all_websites_query.filter_by(is_private=False)
        elif not current_user.is_admin:
            all_websites_query = all_websites_query.filter(
                (Website.is_private == False) |
                (Website.created_by_id == current_user.id) |
                (Website.visible_to.contains(str(current_user.id)))
            )
        
        all_websites = all_websites_query.all()
        total_websites = len(all_websites)
        
        frontend_weights = {}
        for item in items:
            website_id = item.get('id')
            sort_order = item.get('sort_order')
            if website_id is not None and sort_order is not None:
                frontend_weights[website_id] = sort_order
        
        updated_count = 0
        
        for item in items:
            website_id = item.get('id')
            sort_order = item.get('sort_order')
            
            if website_id is not None and sort_order is not None:
                website = Website.query.get(website_id)
                if website:
                    website.sort_order = sort_order
                    updated_count += 1
        
        other_websites = [w for w in all_websites if w.id not in frontend_weights]
        
        used_weights = set(frontend_weights.values())
        available_weights = [i for i in range(1, total_websites + 1) if i not in used_weights]
        
        for i, website in enumerate(other_websites):
            if i < len(available_weights):
                website.sort_order = available_weights[i]
                updated_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'排序顺序已更新 ({updated_count} 个站点)'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'更新排序失败: {str(e)}'}), 500


@bp.route('/api/website/quick-add', methods=['POST'])
@login_required
def quick_add_website():
    """快速添加网站的API接口"""
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '无效的请求数据'}), 400
        
        required_fields = ['url', 'category_id']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'message': f'缺少必要字段: {field}'}), 400
        
        website = Website(
            title=data.get('title', ''),
            url=data['url'],
            description=data.get('description', ''),
            icon=data.get('icon', ''),
            category_id=data['category_id'],
            created_by_id=current_user.id,
            sort_order=data.get('sort_order', 0),
            is_private=data.get('is_private', 0)
        )
        
        db.session.add(website)
        db.session.commit()
        
        category_name = Category.query.get(data['category_id']).name if data['category_id'] else None
        operation_log = OperationLog(
            user_id=current_user.id,
            operation_type='ADD',
            website_id=website.id,
            website_title=website.title,
            website_url=website.url,
            website_icon=website.icon,
            category_id=website.category_id,
            category_name=category_name,
            details='{}'
        )
        db.session.add(operation_log)
        db.session.commit()
        
        # 异步生成向量（如果向量搜索已启用）
        try:
            _trigger_vector_indexing(website.id, category_name)
        except Exception as e:
            current_app.logger.warning(f"触发向量生成失败: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': '网站添加成功',
            'website': {
                'id': website.id,
                'title': website.title,
                'url': website.url,
                'description': website.description,
                'icon': website.icon,
                'category_id': website.category_id
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'添加失败: {str(e)}'}), 500


@bp.route('/api/check_url_exists')
def check_url_exists():
    """检查URL是否已存在"""
    url = request.args.get('url', '').strip()
    exclude_id = request.args.get('exclude_id', None)
    
    if not url:
        return jsonify({'exists': False, 'message': '请提供URL'})
    
    if url.endswith('/'):
        url = url[:-1]
    
    query = Website.query.filter(Website.url.in_([url, url + '/']))
    
    if exclude_id and exclude_id.isdigit():
        query = query.filter(Website.id != int(exclude_id))
    
    website = query.first()
    
    if website:
        return jsonify({
            'exists': True,
            'message': '该链接已存在',
            'website': {
                'id': website.id,
                'title': website.title,
                'url': website.url,
                'description': website.description,
                'icon': website.icon,
                'category_id': website.category_id,
                'category_name': website.category.name if website.category else None,
                'is_private': website.is_private
            }
        })
    
    return jsonify({'exists': False})


def _trigger_vector_indexing(website_id: int, category_name: str = None):
    """
    异步触发向量生成（后台线程执行，不阻塞主流程）
    
    Args:
        website_id: 网站ID
        category_name: 分类名称（如果为None，会从数据库查询）
    """
    def _generate_vector_in_background():
        try:
            # 在后台线程中创建新的应用上下文
            with current_app.app_context():
                settings = SiteSettings.get_settings()
                
                # 检查向量搜索是否启用
                if not (settings and settings.vector_search_enabled and 
                        all([settings.qdrant_url, settings.embedding_model, 
                             settings.ai_api_base_url, settings.ai_api_key])):
                    return
                
                # 获取网站信息
                website = Website.query.get(website_id)
                if not website:
                    return
                
                # 如果没有提供分类名称，从数据库查询
                if category_name is None and website.category_id:
                    category = Category.query.get(website.category_id)
                    cat_name = category.name if category else ""
                else:
                    cat_name = category_name or ""
                
                # 初始化向量服务
                from app.utils.vector_service import EmbeddingClient, QdrantVectorStore, VectorSearchService
                
                embedding_client = EmbeddingClient(
                    api_base_url=settings.ai_api_base_url,
                    api_key=settings.ai_api_key,
                    model_name=settings.embedding_model or 'text-embedding-3-small'
                )
                vector_store = QdrantVectorStore(
                    qdrant_url=settings.qdrant_url,
                    vector_dimension=embedding_client.dimension
                )
                vector_service = VectorSearchService(embedding_client, vector_store)
                
                # 生成向量
                vector_service.index_website(
                    website_id=website.id,
                    title=website.title or "",
                    description=website.description or "",
                    category_name=cat_name,
                    url=website.url or ""
                )
                
                current_app.logger.info(f"网站 {website_id} 向量生成成功")
        except Exception as e:
            current_app.logger.error(f"后台向量生成失败 (website_id={website_id}): {str(e)}")
    
    # 在后台线程中执行，不阻塞主流程
    thread = threading.Thread(target=_generate_vector_in_background, daemon=True)
    thread.start()

