#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""网站管理路由"""

import json
from flask import render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from app import db, csrf
from app.admin import bp
from app.admin.forms import WebsiteForm
from app.admin.decorators import admin_required
from app.admin.utils import trigger_vector_indexing
from app.models import Category, Website, OperationLog


@bp.route('/websites')
@login_required
@admin_required
def websites():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)  # 从URL参数中获取每页显示数量
    category_id = request.args.get('category_id', type=int)
    
    # 构建查询
    query = Website.query
    
    # 应用分类筛选
    if category_id:
        query = query.filter_by(category_id=category_id)
    
    # 获取分页数据
    pagination = query.order_by(Website.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    websites = pagination.items
    
    # 获取所有分类供筛选使用
    categories = Category.query.order_by(Category.order.asc()).all()
    
    return render_template(
        'admin/websites.html',
        title='网站管理',
        websites=websites,
        pagination=pagination,
        categories=categories
    )


@bp.route('/api/website/batch-delete', methods=['POST'])
@login_required
@admin_required
@csrf.exempt  # 豁免CSRF保护
def batch_delete_websites():
    """批量删除网站"""
    try:
        data = request.get_json()
        if not data or 'ids' not in data:
            return jsonify({'success': False, 'message': '无效的请求数据'}), 400
            
        website_ids = data['ids']
        if not isinstance(website_ids, list):
            return jsonify({'success': False, 'message': '无效的ID列表'}), 400
        
        # 先获取所有要删除的网站信息，用于记录操作日志
        websites = Website.query.filter(Website.id.in_(website_ids)).all()
        
        for website in websites:
            # 记录删除操作
            details = {
                'description': website.description,
                'is_private': website.is_private,
                'is_featured': website.is_featured
            }
            
            operation_log = OperationLog(
                user_id=current_user.id,
                operation_type='DELETE',
                website_id=None,  # 删除后ID不存在
                website_title=website.title,
                website_url=website.url,
                website_icon=website.icon,
                category_id=website.category_id,
                category_name=website.category.name if website.category else None,
                details=json.dumps(details)
            )
            
            db.session.add(operation_log)
        
        # 删除选中的网站
        deleted_count = Website.query.filter(Website.id.in_(website_ids)).delete(synchronize_session=False)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'成功删除 {deleted_count} 个网站'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


@bp.route('/api/website/batch-update', methods=['POST'])
@login_required
@admin_required
@csrf.exempt  # 豁免CSRF保护
def batch_update_websites():
    """批量更新网站"""
    try:
        data = request.get_json()
        if not data or 'ids' not in data or 'data' not in data:
            return jsonify({'success': False, 'message': '无效的请求数据'}), 400
            
        website_ids = data['ids']
        update_data = data['data']
        
        if not isinstance(website_ids, list):
            return jsonify({'success': False, 'message': '无效的ID列表'}), 400
            
        # 更新选中的网站
        websites = Website.query.filter(Website.id.in_(website_ids)).all()
        updated_count = 0
        for website in websites:
            if 'is_private' in update_data:
                website.is_private = update_data['is_private']
                updated_count += 1
                
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'成功更新 {updated_count} 个网站'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'更新失败: {str(e)}'}), 500


@bp.route('/website/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_website():
    form = WebsiteForm()
    if form.validate_on_submit():
        website = Website(
            title=form.title.data,
            url=form.url.data,
            description=form.description.data,
            icon=form.icon.data,
            category_id=form.category_id.data,
            is_featured=form.is_featured.data,
            is_private=form.is_private.data,
            sort_order=form.sort_order.data,  # 使用表单中的排序权重
            created_by_id=current_user.id
        )
        db.session.add(website)
        db.session.commit()
        
        try:
            # 记录添加操作
            category = Category.query.get(form.category_id.data) if form.category_id.data else None
            category_name = category.name if category else None
            
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
        except Exception as e:
            # 记录日志失败不影响主功能
            current_app.logger.error(f"记录添加操作日志失败: {str(e)}")
        
        # 异步生成向量（如果向量搜索已启用）
        try:
            trigger_vector_indexing(website.id, category_name)
        except Exception as e:
            current_app.logger.warning(f"触发向量生成失败: {str(e)}")
        
        flash('网站添加成功', 'success')
        return redirect(url_for('admin.websites'))
    return render_template('admin/website_form.html', title='添加网站', form=form)


@bp.route('/website/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_website(id):
    website = Website.query.get_or_404(id)
    form = WebsiteForm(obj=website)
    
    if form.validate_on_submit():
        # 记录修改前的值
        old_title = website.title
        old_url = website.url
        old_description = website.description
        old_category_id = website.category_id
        old_category_name = website.category.name if website.category else None
        old_is_featured = website.is_featured
        old_is_private = website.is_private
        old_sort_order = website.sort_order
        
        # 更新数据
        website.title = form.title.data
        website.url = form.url.data
        website.description = form.description.data
        website.icon = form.icon.data
        website.category_id = form.category_id.data
        website.is_featured = form.is_featured.data
        website.is_private = form.is_private.data
        website.sort_order = form.sort_order.data
        
        db.session.commit()
        
        # 记录修改操作
        changes = {}
        if old_title != website.title:
            changes['title'] = {'old': old_title, 'new': website.title}
        if old_url != website.url:
            changes['url'] = {'old': old_url, 'new': website.url}
        if old_description != website.description:
            changes['description'] = {'old': old_description, 'new': website.description}
        if old_category_id != website.category_id:
            new_category_name = website.category.name if website.category else None
            changes['category'] = {
                'old': {'id': old_category_id, 'name': old_category_name}, 
                'new': {'id': website.category_id, 'name': new_category_name}
            }
        if old_is_featured != website.is_featured:
            changes['is_featured'] = {'old': old_is_featured, 'new': website.is_featured}
        if old_is_private != website.is_private:
            changes['is_private'] = {'old': old_is_private, 'new': website.is_private}
        if old_sort_order != website.sort_order:
            changes['sort_order'] = {'old': old_sort_order, 'new': website.sort_order}
        
        if changes:  # 仅当有变更时才记录
            # 获取分类名称
            category = Category.query.get(form.category_id.data) if form.category_id.data else None
            category_name = category.name if category else None
            
            try:
                # 创建操作日志
                operation_log = OperationLog(
                    user_id=current_user.id,
                    operation_type='MODIFY',
                    website_id=website.id,
                    website_title=website.title,
                    website_url=website.url,
                    website_icon=website.icon,
                    category_id=website.category_id,
                    category_name=category_name,
                    details=json.dumps(changes)
                )
                db.session.add(operation_log)
                db.session.commit()
            except Exception as e:
                current_app.logger.error(f"记录修改操作日志失败: {str(e)}")
        
        # 检查是否需要更新向量（标题、描述或分类变化时）
        needs_vector_update = (
            old_title != website.title or
            old_description != website.description or
            old_category_id != website.category_id
        )
        
        if needs_vector_update:
            try:
                new_category_name = website.category.name if website.category else None
                trigger_vector_indexing(website.id, new_category_name)
            except Exception as e:
                current_app.logger.warning(f"触发向量更新失败: {str(e)}")
        
        flash('网站更新成功', 'success')
        return redirect(url_for('admin.websites'))
        
    return render_template('admin/website_form.html', title='编辑网站', form=form)


@bp.route('/website/delete/<int:id>')
@login_required
@admin_required
def delete_website(id):
    website = Website.query.get_or_404(id)
    
    # 记录删除操作
    details = {
        'description': website.description,
        'is_private': website.is_private,
        'is_featured': website.is_featured
    }
    
    operation_log = OperationLog(
        user_id=current_user.id,
        operation_type='DELETE',
        website_id=None,  # 删除后ID不存在
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
    
    flash('网站删除成功', 'success')
    return redirect(url_for('admin.websites'))

