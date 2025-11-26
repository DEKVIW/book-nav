from datetime import datetime, timezone, timedelta
from functools import wraps
import os
from flask import render_template, redirect, url_for, flash, request, abort, jsonify, session, current_app, send_file, send_from_directory, Response
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.utils import secure_filename
from app import db, csrf
from app.admin import bp
from app.admin.forms import CategoryForm, WebsiteForm, InvitationForm, UserEditForm, SiteSettingsForm, DataImportForm, BackgroundForm
from app.models import Category, Website, InvitationCode, User, SiteSettings, OperationLog, Background, DeadlinkCheck
from app.main.utils import get_website_icon
import time
import json
import threading
from queue import Queue
from urllib.parse import urlparse
import requests
import shutil
import sqlite3
import tempfile
import random
import uuid
import secrets
from werkzeug.security import generate_password_hash
from sqlalchemy import or_, func, desc, extract, case
from sqlalchemy.exc import SQLAlchemyError
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import io
import queue


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

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

def superadmin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_superadmin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@bp.route('/')
@login_required
@admin_required
def index():
    stats = {
        'users': User.query.count(),
        'active_users': User.query.filter(User.created_at > datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)).count(),
        'categories': Category.query.count(),
        'websites': Website.query.count(),
        'invitation_codes': InvitationCode.query.filter_by(is_active=True, used_by_id=None).count()
    }
    return render_template('admin/index.html', title='管理面板', stats=stats)

# 分类管理
@bp.route('/categories')
@login_required
@admin_required
def categories():
    categories = Category.query.order_by(Category.order.desc()).all()
    return render_template('admin/categories.html', title='分类管理', categories=categories)

@bp.route('/category/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_category():
    form = CategoryForm()
    if form.validate_on_submit():
        category = Category(
            name=form.name.data,
            description=form.description.data,
            icon=form.icon.data,
            color=form.color.data,
            order=form.order.data,
            display_limit=form.display_limit.data,
            parent_id=form.parent_id.data
        )
        db.session.add(category)
        db.session.commit()
        flash('分类添加成功', 'success')
        return redirect(url_for('admin.categories'))
    return render_template('admin/category_form.html', title='添加分类', form=form)

@bp.route('/category/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_category(id):
    category = Category.query.get_or_404(id)
    form = CategoryForm(obj=category)
    
    # 修复初始选择
    if form.parent_id.data is None:
        form.parent_id.data = 0
        
    if form.validate_on_submit():
        # 检查是否尝试将分类设为自身的子分类或后代的子分类
        if form.parent_id.data and form.parent_id.data == id:
            flash('分类不能作为自身的子分类', 'danger')
            return render_template('admin/category_form.html', title='编辑分类', form=form)
            
        # 获取所有后代ID
        descendants = [c.id for c in category.get_all_descendants()] if hasattr(category, 'get_all_descendants') else []
        if form.parent_id.data in descendants:
            flash('分类不能设置为其后代分类的子分类', 'danger')
            return render_template('admin/category_form.html', title='编辑分类', form=form)
            
        form.populate_obj(category)
        db.session.commit()
        flash('分类更新成功', 'success')
        return redirect(url_for('admin.categories'))
    return render_template('admin/category_form.html', title='编辑分类', form=form)

@bp.route('/category/delete/<int:id>')
@login_required
@admin_required
def delete_category(id):
    category = Category.query.get_or_404(id)
    if Website.query.filter_by(category_id=id).first():
        flash('该分类下存在网站，无法删除', 'danger')
    else:
        db.session.delete(category)
        db.session.commit()
        flash('分类删除成功', 'success')
    return redirect(url_for('admin.categories'))

# 网站管理
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
            import json
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
            _trigger_vector_indexing(website.id, category_name)
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
            import json
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
                _trigger_vector_indexing(website.id, new_category_name)
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
    import json
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

# 邀请码管理
@bp.route('/invitations')
@login_required
@admin_required
def invitations():
    active_codes = InvitationCode.query.filter_by(is_active=True, used_by_id=None).all()
    used_codes = InvitationCode.query.filter(InvitationCode.used_by_id.isnot(None)).all()
    form = InvitationForm()
    return render_template('admin/invitations.html', title='邀请码管理', 
                           active_codes=active_codes, used_codes=used_codes, form=form)

@bp.route('/invitation/generate', methods=['POST'])
@login_required
@admin_required
def generate_invitation():
    form = InvitationForm()
    if form.validate_on_submit():
        count = min(form.count.data, 10)  # 限制一次最多生成10个
        for _ in range(count):
            code = InvitationCode(
                code=InvitationCode.generate_code(),
                created_by_id=current_user.id
            )
            db.session.add(code)
        db.session.commit()
        # 使用URL参数传递消息而不是flash
        return redirect(url_for('admin.invitations', flash_message=f'成功生成{count}个邀请码', flash_category='success'))
    return redirect(url_for('admin.invitations'))

@bp.route('/invitation/delete/<int:id>')
@login_required
@admin_required
def delete_invitation(id):
    invitation = InvitationCode.query.get_or_404(id)
    if invitation.used_by_id is not None:
        # 使用URL参数传递错误消息
        return redirect(url_for('admin.invitations', flash_message='该邀请码已被使用，无法删除', flash_category='danger'))
    else:
        db.session.delete(invitation)
        db.session.commit()
        # 使用URL参数传递成功消息
        return redirect(url_for('admin.invitations', flash_message='邀请码删除成功', flash_category='success'))
    return redirect(url_for('admin.invitations'))

# 用户管理
@bp.route('/users')
@login_required
@superadmin_required
def users():
    users = User.query.all()
    return render_template('admin/users.html', title='用户管理', users=users)


@bp.route('/user/detail/<int:id>')
@login_required
@superadmin_required
def user_detail(id):
    user = User.query.get_or_404(id)
    websites = Website.query.filter_by(created_by_id=user.id).all()
    
    page_size = {
        'all': request.args.get('all_per_page', 10, type=int),
        'added': request.args.get('added_per_page', 10, type=int),
        'modified': request.args.get('modified_per_page', 10, type=int),
        'deleted': request.args.get('deleted_per_page', 10, type=int)
    }
    page = {
        'all': request.args.get('all_page', 1, type=int),
        'added': request.args.get('added_page', 1, type=int),
        'modified': request.args.get('modified_page', 1, type=int),
        'deleted': request.args.get('deleted_page', 1, type=int)
    }
    
    # 查询用户的操作记录
    added_records_query = OperationLog.query.filter_by(
        user_id=user.id, 
        operation_type='ADD'
    ).order_by(OperationLog.created_at.desc())
    
    modified_records_query = OperationLog.query.filter_by(
        user_id=user.id, 
        operation_type='MODIFY'
    ).order_by(OperationLog.created_at.desc())
    
    deleted_records_query = OperationLog.query.filter_by(
        user_id=user.id, 
        operation_type='DELETE'
    ).order_by(OperationLog.created_at.desc())
    
    # 全部操作记录查询
    all_records_query = OperationLog.query.filter_by(
        user_id=user.id
    ).order_by(OperationLog.created_at.desc())
    
    # 使用分页
    all_pagination = all_records_query.paginate(
        page=page['all'], 
        per_page=page_size['all'],
        error_out=False
    )
    
    added_pagination = added_records_query.paginate(
        page=page['added'], 
        per_page=page_size['added'],
        error_out=False
    )
    modified_pagination = modified_records_query.paginate(
        page=page['modified'], 
        per_page=page_size['modified'],
        error_out=False
    )
    deleted_pagination = deleted_records_query.paginate(
        page=page['deleted'], 
        per_page=page_size['deleted'],
        error_out=False
    )
    
    all_records = all_pagination.items
    added_records = added_pagination.items
    modified_records = modified_pagination.items
    deleted_records = deleted_pagination.items
    
    return render_template(
        'admin/user_detail.html', 
        title='用户详情', 
        user=user, 
        websites=websites,
        all_records=all_records,
        added_records=added_records,
        modified_records=modified_records,
        deleted_records=deleted_records,
        all_pagination=all_pagination,
        added_pagination=added_pagination,
        modified_pagination=modified_pagination,
        deleted_pagination=deleted_pagination
    )


@bp.route('/user/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(id):
    """编辑用户"""
    user = User.query.get_or_404(id)
    
    # 普通管理员不能编辑超级管理员
    if user.is_superadmin and not current_user.is_superadmin:
        flash('权限不足，无法编辑超级管理员', 'danger')
        return redirect(url_for('admin.users'))
    
    # 创建表单并使用用户数据进行预填充
    form = UserEditForm(user.username, user.email, obj=user)
    
    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data
        
        # 如果提供了新密码，则更新密码
        if form.password.data:
            user.set_password(form.password.data)
        
        # 只有超级管理员可以更改管理员权限，普通管理员不能改
        user.is_admin = form.is_admin.data
        
        # 超级管理员权限只有当前用户是超级管理员时才能赋予他人
        if current_user.is_superadmin and form.is_superadmin.data:
            user.is_superadmin = True
        
        # 处理头像上传
        avatar_file = request.files.get('avatar')
        if avatar_file and avatar_file.filename:
            # 确保文件名安全
            filename = secure_filename(avatar_file.filename)
            # 添加时间戳避免文件名冲突
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            avatar_filename = f"{timestamp}_{user.id}_{filename}"
            
            # 确保avatars目录存在
            avatar_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'avatars')
            os.makedirs(avatar_dir, exist_ok=True)
            
            # 保存文件
            avatar_path = os.path.join(avatar_dir, avatar_filename)
            try:
                avatar_file.save(avatar_path)
                # 更新用户头像URL
                user.avatar = url_for('static', filename=f'uploads/avatars/{avatar_filename}')
                flash('头像已更新', 'success')
            except Exception as e:
                flash(f'头像上传失败: {str(e)}', 'danger')
        
        db.session.commit()
        flash('用户信息更新成功!', 'success')
        return redirect(url_for('admin.users'))
    
    return render_template('admin/user_edit.html', title='编辑用户', form=form, user=user)

# 添加删除用户功能
@bp.route('/user/delete/<int:id>')
@login_required
@superadmin_required
def delete_user(id):
    user = User.query.get_or_404(id)
    
    # 不能删除自己
    if user.id == current_user.id:
        flash('不能删除当前登录的用户', 'danger')
        return redirect(url_for('admin.users'))
    
    # 删除前检查关联的网站，可以选择转移或删除
    websites_count = Website.query.filter_by(created_by_id=user.id).count()
    if websites_count > 0:
        flash(f'该用户已创建了 {websites_count} 个网站，请先处理这些内容', 'warning')
        return redirect(url_for('admin.user_detail', id=user.id))
    
    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f'用户 {username} 已被删除', 'success')
    return redirect(url_for('admin.users'))

# 站点设置管理
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
            
            # 检查向量搜索配置是否完整
            vector_configured = all([
                settings.vector_search_enabled,
                settings.qdrant_url,
                settings.ai_api_base_url,
                settings.ai_api_key
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
    from app.utils.ai_search import AISearchService
    from app.models import SiteSettings
    
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
        
        # 创建AI服务并测试
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
        current_app.logger.error(f"AI配置测试失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'测试失败: {str(e)}'
        }), 400

def save_image(file_data, subfolder):
    """保存上传的图片到static/uploads目录"""
    if not file_data:
        return None
        
    # 确保存储目录存在
    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', subfolder)
    os.makedirs(upload_dir, exist_ok=True)
    
    # 生成唯一文件名并保存文件
    filename = secure_filename(file_data.filename)
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    unique_filename = f"{timestamp}_{filename}"
    file_path = os.path.join(upload_dir, unique_filename)
    
    try:
        file_data.save(file_path)
        return unique_filename
    except Exception as e:
        flash(f'图片上传失败: {str(e)}', 'danger')
        return None 


# 处理操作日志相关API
@bp.route('/api/operation-log/delete', methods=['POST'])
@login_required
@admin_required
def delete_operation_log():
    data = request.json
    log_id = data.get('id')
    
    if not log_id:
        return jsonify({'success': False, 'message': '未提供日志ID'})
    
    log = OperationLog.query.get(log_id)
    if not log:
        return jsonify({'success': False, 'message': '日志不存在'})
    
    db.session.delete(log)
    db.session.commit()
    
    return jsonify({'success': True, 'message': '日志删除成功'})

@bp.route('/api/operation-log/batch-delete', methods=['POST'])
@login_required
@admin_required
def batch_delete_operation_logs():
    data = request.json
    log_ids = data.get('ids', [])
    
    if not log_ids:
        return jsonify({'success': False, 'message': '未提供日志ID'})
    
    logs = OperationLog.query.filter(OperationLog.id.in_(log_ids)).all()
    for log in logs:
        db.session.delete(log)
    
    db.session.commit()
    
    return jsonify({'success': True, 'message': f'已删除 {len(logs)} 条日志'})

@bp.route('/api/operation-log/clear-all/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def clear_all_operation_logs(user_id):
    """清空指定用户的所有操作记录"""
    try:
        # 验证用户存在
        user = User.query.get_or_404(user_id)
        
        # 获取用户所有操作记录数量
        count = OperationLog.query.filter_by(user_id=user_id).count()
        
        # 删除所有操作记录
        OperationLog.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'已清空用户 {user.username} 的所有操作记录，共 {count} 条'
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"清空操作记录失败: {str(e)}")
        return jsonify({'success': False, 'message': f'操作失败: {str(e)}'}), 500

# 数据库导入导出
import io
import shutil
import sqlite3
import tempfile
from datetime import datetime
from werkzeug.utils import secure_filename
from app.admin.forms import DataImportForm

@bp.route('/data-management')
@login_required
@superadmin_required
def data_management():
    """数据管理页面"""
    import_form = DataImportForm()
    return render_template('admin/data_management.html', title='数据管理', import_form=import_form)

@bp.route('/export-data')
@login_required
@superadmin_required
def export_data():
    """导出数据库"""
    # 获取导出格式，默认为本项目格式
    export_format = request.args.get('format', 'native')
    
    # 确定时间戳
    timestamp = datetime.now().strftime('%Y%m%d%H%M')
    if export_format == 'onenav':
        filename = f"booknav_export_onenav_{timestamp}.db3"
    else:
        filename = f"booknav_export_{timestamp}.db3"
    
    # 创建临时文件
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db3')
    temp_db_path = temp_db.name
    temp_db.close()
    
    try:
        # 复制当前数据库
        db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        current_app.logger.info(f"准备从 {db_path} 导出数据到 {temp_db_path}")
        
        # 数据库路径可能是相对路径，需要转换为绝对路径
        if not os.path.isabs(db_path):
            db_path = os.path.join(current_app.root_path, db_path)
            current_app.logger.info(f"转换为绝对路径: {db_path}")
        
        # 检查源文件是否存在
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"找不到数据库文件: {db_path}")
        
        # 复制数据库文件
        shutil.copy2(db_path, temp_db_path)
        current_app.logger.info(f"数据库文件已复制")
        
        # 如果选择OneNav格式，则进行格式转换
        if export_format == 'onenav':
            current_app.logger.info("将导出转换为OneNav格式")
            if not convert_to_onenav_format(temp_db_path):
                raise Exception("转换为OneNav格式失败")
        
        # 读取临时文件的内容
        with open(temp_db_path, 'rb') as f:
            db_data = f.read()
            
        # 删除临时文件
        os.unlink(temp_db_path)
        
        # 将数据返回为可下载的文件
        current_app.logger.info(f"数据导出成功，大小：{len(db_data)}字节")
        return send_file(
            io.BytesIO(db_data),
            as_attachment=True,
            download_name=filename,
            mimetype='application/octet-stream'
        )
    except Exception as e:
        current_app.logger.error(f"导出数据失败: {str(e)}")
        # 确保临时文件被删除
        if os.path.exists(temp_db_path):
            try:
                os.unlink(temp_db_path)
            except:
                pass
        flash(f'导出数据失败: {str(e)}', 'danger')
        return redirect(url_for('admin.data_management'))

@bp.route('/import-data', methods=['POST'])
@login_required
@superadmin_required
def import_data():
    """导入数据库"""
    form = DataImportForm()
    if form.validate_on_submit():
        db_file = form.db_file.data
        import_type = form.import_type.data
        
        # 检查文件是否存在
        if not db_file:
            flash('请选择要导入的数据库文件', 'danger')
            return redirect(url_for('admin.data_management'))
        
        # 创建临时文件保存上传内容
        temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db3')
        temp_db_path = temp_db.name
        temp_db.close()
        
        try:
            # 保存上传的文件
            db_file.save(temp_db_path)
            
            # 在导入前先创建一个备份（安全措施）
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            backup_filename = f"pre_import_backup_{timestamp}.db3"
            backup_dir = os.path.join(current_app.root_path, 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            backup_path = os.path.join(backup_dir, backup_filename)
            
            # 复制当前数据库作为备份
            db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
            if not os.path.isabs(db_path):
                db_path = os.path.join(current_app.root_path, db_path)
            shutil.copy2(db_path, backup_path)
            current_app.logger.info(f"已创建数据库备份: {backup_path}")
            
            # 自动检测数据库格式
            if is_project_db(temp_db_path):
                # 如果是本项目数据库格式
                current_app.logger.info("检测到本项目数据库格式")
                success, cat_count, link_count = import_project_db(temp_db_path, import_type, current_user.id)
                if success:
                    flash(f'数据导入成功! 导入了{cat_count}个分类和{link_count}个链接', 'success')
                else:
                    flash('数据导入失败', 'danger')
            elif is_onenav_db(temp_db_path):
                # 如果是OneNav格式
                current_app.logger.info("检测到OneNav数据库格式")
                
                # 如果是替换模式，清空现有数据
                if import_type == "replace":
                    current_app.logger.info("执行替换模式，清空现有数据...")
                    Website.query.delete()
                    Category.query.delete()
                    db.session.commit()
                
                try:
                    results = import_onenav_direct(temp_db_path, import_type, current_user.id)
                    flash(f'导入成功! {results["cats_count"]}个分类, {results["links_count"]}个链接', 'success')
                except Exception as e:
                    flash(f'导入过程中发生错误: {str(e)}', 'danger')
                    current_app.logger.error(f"导入错误: {str(e)}")
            else:
                # 如果格式无法识别
                flash('无法识别的数据库格式', 'danger')
                
            # 删除临时文件
            os.unlink(temp_db_path)
                
        except Exception as e:
            flash(f'数据导入失败: {str(e)}', 'danger')
            current_app.logger.error(f"数据导入失败: {str(e)}")
            if os.path.exists(temp_db_path):
                os.unlink(temp_db_path)
                
        return redirect(url_for('admin.data_management'))
        
    # 验证失败
    for field, errors in form.errors.items():
        for error in errors:
            flash(f'{getattr(form, field).label.text}: {error}', 'danger')
            
    return redirect(url_for('admin.data_management'))

def import_onenav_direct(db_path, import_type, admin_id):
    """直接导入OneNav数据库，集成自migrate_onenav.py"""
    results = {"cats_count": 0, "links_count": 0}
    
    # 源数据库连接
    source_conn = sqlite3.connect(db_path)
    source_conn.row_factory = sqlite3.Row
    
    # 如果是替换模式，清空现有数据
    if import_type == "replace":
        current_app.logger.info("执行替换模式，清空现有数据...")
        Website.query.delete()
        Category.query.delete()
        db.session.commit()
    
    # 首先迁移分类
    category_mapping = {}  # 存储旧ID到新ID的映射
    cursor = source_conn.cursor()
    
    # 获取所有分类
    cursor.execute("SELECT * FROM on_categorys ORDER BY weight DESC")
    categories = cursor.fetchall()
    
    # 获取现有分类（合并模式使用）
    existing_categories = {}
    if import_type == "merge":
        existing_categories = {c.name.lower(): c for c in Category.query.all()}
    
    # 先处理一级分类
    for category in categories:
        if category['fid'] == 0:  # 一级分类
            # 检查合并模式下是否已存在同名分类
            cat_name = category['name'].lower()
            if import_type == "merge" and cat_name in existing_categories:
                # 使用现有分类ID
                category_mapping[category['id']] = existing_categories[cat_name].id
            else:
                # 创建新分类
                new_category = Category(
                    name=category['name'],
                    description=category['description'] or '',
                    icon=map_icon(category['font_icon']),
                    order=category['weight']
                )
                db.session.add(new_category)
                db.session.flush()  # 获取新ID
                
                # 保存ID映射关系
                category_mapping[category['id']] = new_category.id
                if import_type == "merge":
                    existing_categories[cat_name] = new_category
    
    # 再处理二级分类
    for category in categories:
        if category['fid'] != 0:  # 二级分类
            # 检查父分类是否已迁移
            if category['fid'] in category_mapping:
                # 检查合并模式下是否已存在同名分类
                cat_name = category['name'].lower()
                if import_type == "merge" and cat_name in existing_categories:
                    # 使用现有分类ID
                    category_mapping[category['id']] = existing_categories[cat_name].id
                else:
                    # 创建新分类
                    new_category = Category(
                        name=category['name'],
                        description=category['description'] or '',
                        icon=map_icon(category['font_icon']),
                        order=category['weight'],
                        parent_id=category_mapping[category['fid']]
                    )
                    db.session.add(new_category)
                    db.session.flush()
                    
                    # 保存ID映射关系
                    category_mapping[category['id']] = new_category.id
                    if import_type == "merge":
                        existing_categories[cat_name] = new_category
    
    db.session.commit()
    results["cats_count"] = len(category_mapping)
    
    # 获取现有URL，避免重复导入
    existing_urls = {}
    if import_type == "merge":
        existing_urls = {w.url.lower(): True for w in Website.query.all()}
    
    # 迁移链接
    cursor.execute("SELECT * FROM on_links ORDER BY fid, weight DESC")
    links = cursor.fetchall()
    
    migrated_count = 0
    skipped_count = 0
    
    for link in links:
        # 检查分类是否已迁移
        if link['fid'] in category_mapping:
            # 检查URL是否重复（合并模式）
            url_lower = link['url'].lower()
            if import_type == "merge" and url_lower in existing_urls:
                skipped_count += 1
                continue
                
            # 转换时间戳为datetime
            try:
                add_time = datetime.fromtimestamp(int(link['add_time']))
            except:
                add_time = datetime.now()
            
            try:
                new_website = Website(
                    title=link['title'],
                    url=link['url'],
                    description=link['description'] or '',
                    icon=link['font_icon'] or '',
                    category_id=category_mapping[link['fid']],
                    created_by_id=admin_id,
                    created_at=add_time,
                    sort_order=link['weight'] or 0,
                    is_private=(link['property'] == 1),  # 假设property=1表示私有
                    views=link['click'] or 0
                )
                db.session.add(new_website)
                migrated_count += 1
                if import_type == "merge":
                    existing_urls[url_lower] = True
                
                # 每100条提交一次，避免内存问题
                if migrated_count % 100 == 0:
                    db.session.commit()
            except Exception as e:
                skipped_count += 1
                current_app.logger.error(f"链接导入错误 {link['title']}: {str(e)}")
        else:
            skipped_count += 1
    
    # 保存所有更改
    db.session.commit()
    results["links_count"] = migrated_count
    
    # 关闭连接
    source_conn.close()
    return results

def map_icon(font_icon):
    """处理OneNav的图标格式"""
    # 如果是URL格式，直接返回
    if font_icon and (font_icon.startswith('http://') or font_icon.startswith('https://')):
        return font_icon
    
    # 如果是Font Awesome图标格式，保留原格式
    if font_icon and ('fa-' in font_icon):
        # 确保格式正确，添加fa前缀如果没有的话
        if not font_icon.startswith('fa ') and not font_icon.startswith('fas '):
            return 'fa ' + font_icon.strip()
        return font_icon
    
    # 如果不是Font Awesome格式但有值，转为Bootstrap格式
    if font_icon:
        return 'bi-' + font_icon.strip()
    
    # 默认图标
    return 'bi-link'

def is_valid_sqlite_db(file_path):
    """检查文件是否为有效的SQLite数据库"""
    try:
        conn = sqlite3.connect(file_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        cursor.fetchall()
        conn.close()
        return True
    except sqlite3.Error:
        return False

def is_onenav_db(file_path):
    """检查是否为OneNav格式的数据库"""
    try:
        conn = sqlite3.connect(file_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='on_categorys' OR name='on_links'")
        result = cursor.fetchall()
        conn.close()
        return len(result) >= 2  # 至少包含分类和链接表
    except sqlite3.Error:
        return False

def convert_to_onenav_format(db_path):
    """将系统数据库转换为OneNav格式（导出时使用）"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 创建OneNav表结构
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS on_categorys (
            id INTEGER PRIMARY KEY,
            name TEXT(32),
            add_time TEXT(10),
            up_time TEXT(10),
            weight integer(3),
            property integer(1),
            description TEXT(128),
            font_icon TEXT(32),
            fid INTEGER
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS on_links (
            id INTEGER PRIMARY KEY,
            fid INTEGER(5),
            title TEXT(64),
            url TEXT(256),
            description TEXT(256),
            add_time TEXT(10),
            up_time TEXT(10),
            weight integer(3),
            property integer(1),
            click INTEGER,
            topping INTEGER,
            url_standby TEXT(256),
            font_icon TEXT(512),
            check_status INTEGER,
            last_checked_time TEXT
        )
        ''')
        
        # 转换分类数据 - 修改SQL，为order字段添加引号避免SQL关键字冲突
        cursor.execute('''
        SELECT c.id, c.name, c.description, c.icon, c."order", c.parent_id, c.created_at
        FROM category c
        ''')
        categories = cursor.fetchall()
        
        for cat in categories:
            cat_id, name, desc, icon, order, parent_id, created_at = cat
            # 转换时间戳
            try:
                # 处理created_at为None或字符串的情况
                if created_at is None:
                    add_time = int(datetime.now().timestamp())
                elif isinstance(created_at, str):
                    # 尝试解析字符串格式的时间
                    try:
                        dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                        add_time = int(dt.timestamp())
                    except:
                        add_time = int(datetime.now().timestamp())
                else:
                    # 尝试作为datetime对象处理
                    add_time = int(created_at.timestamp())
            except:
                add_time = int(datetime.now().timestamp())
                
            up_time = add_time
            weight = order or 0
            property = 0  # 默认公开
            fid = parent_id or 0  # 父分类ID
            
            # 确保值的类型正确
            name = str(name) if name else ''
            desc = str(desc) if desc else ''
            icon = str(icon) if icon else ''
            
            cursor.execute('''
            INSERT INTO on_categorys (id, name, add_time, up_time, weight, property, description, font_icon, fid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (cat_id, name, str(add_time), str(up_time), weight, property, desc, icon, fid))
        
        # 转换链接数据
        cursor.execute('''
        SELECT w.id, w.category_id, w.title, w.url, w.description, w.icon, w.created_at, 
               w.sort_order, w.is_private, w.views
        FROM website w
        ''')
        websites = cursor.fetchall()
        
        for site in websites:
            site_id, category_id, title, url, desc, icon, created_at, sort_order, is_private, views = site
            # 转换时间戳
            try:
                # 处理created_at为None或字符串的情况
                if created_at is None:
                    add_time = int(datetime.now().timestamp())
                elif isinstance(created_at, str):
                    # 尝试解析字符串格式的时间
                    try:
                        dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                        add_time = int(dt.timestamp())
                    except:
                        add_time = int(datetime.now().timestamp())
                else:
                    # 尝试作为datetime对象处理
                    add_time = int(created_at.timestamp())
            except:
                add_time = int(datetime.now().timestamp())
                
            up_time = add_time
            weight = sort_order or 0
            property = 1 if is_private else 0
            fid = category_id or 0
            
            # 确保值的类型正确
            title = str(title) if title else ''
            url = str(url) if url else ''
            desc = str(desc) if desc else ''
            icon = str(icon) if icon else ''
            views = int(views) if views else 0
            
            cursor.execute('''
            INSERT INTO on_links (id, fid, title, url, description, add_time, up_time, weight, property, click, 
                                topping, font_icon, check_status, last_checked_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (site_id, fid, title, url, desc, str(add_time), str(up_time), weight, property, 
                views, 0, icon, 1, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        
        conn.commit()
        conn.close()
        current_app.logger.info(f"成功将数据转换为OneNav格式，导出{len(categories)}个分类和{len(websites)}个链接")
        return True
    except Exception as e:
        current_app.logger.error(f"转换数据库格式失败: {str(e)}")
        return False

# 添加清空链接数据的路由
@bp.route('/clear-websites', methods=['POST'])
@login_required
@superadmin_required
def clear_websites():
    """清空所有网站链接数据"""
    try:
        # 获取当前链接数量
        website_count = Website.query.count()
        
        # 删除所有链接数据
        Website.query.delete()
        
        # 提交更改
        db.session.commit()
        
        # 记录日志
        current_app.logger.info(f"已清空所有网站链接数据，共删除{website_count}条记录")
        
        return jsonify({'success': True, 'message': f'已成功删除{website_count}条链接数据'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"清空链接数据失败: {str(e)}")
        return jsonify({'success': False, 'message': f'操作失败: {str(e)}'})

# 添加清空所有数据的路由
@bp.route('/clear-all-data', methods=['POST'])
@login_required
@superadmin_required
def clear_all_data():
    """清空所有网站和分类数据"""
    try:
        # 获取当前数据量
        website_count = Website.query.count()
        category_count = Category.query.count()
        
        # 先删除所有链接数据（因为有外键约束）
        Website.query.delete()
        
        # 再删除所有分类数据
        Category.query.delete()
        
        # 提交更改
        db.session.commit()
        
        # 记录日志
        current_app.logger.info(f"已清空所有数据，共删除{website_count}条链接和{category_count}个分类")
        
        return jsonify({
            'success': True, 
            'message': f'已成功删除{website_count}条链接和{category_count}个分类'
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"清空所有数据失败: {str(e)}")
        return jsonify({'success': False, 'message': f'操作失败: {str(e)}'})

# 添加备份管理功能
@bp.route('/backup-data')
@login_required
@superadmin_required
def backup_data():
    """创建数据库备份"""
    # 确定时间戳和文件名
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    filename = f"booknav_{timestamp}.db3"
    
    # 确保备份目录存在
    backup_dir = os.path.join(current_app.root_path, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    
    # 备份文件路径
    backup_path = os.path.join(backup_dir, filename)
    
    try:
        # 复制当前数据库
        db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        current_app.logger.info(f"准备备份数据库到 {backup_path}")
        
        # 数据库路径可能是相对路径，需要转换为绝对路径
        if not os.path.isabs(db_path):
            db_path = os.path.join(current_app.root_path, db_path)
            current_app.logger.info(f"转换为绝对路径: {db_path}")
        
        # 检查源文件是否存在
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"找不到数据库文件: {db_path}")
        
        # 复制数据库文件
        shutil.copy2(db_path, backup_path)
        current_app.logger.info(f"数据库备份成功: {backup_path}")
        
        flash('数据库备份成功', 'success')
        return redirect(url_for('admin.backup_list'))
    except Exception as e:
        current_app.logger.error(f"数据库备份失败: {str(e)}")
        flash(f'数据库备份失败: {str(e)}', 'danger')
        return redirect(url_for('admin.backup_list'))

@bp.route('/backup-list')
@login_required
@superadmin_required
def backup_list():
    """备份列表管理页面"""
    # 确保备份目录存在
    backup_dir = os.path.join(current_app.root_path, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    
    # 获取所有备份文件
    backups = []
    for filename in os.listdir(backup_dir):
        if filename.endswith('.db3'):
            file_path = os.path.join(backup_dir, filename)
            file_stats = os.stat(file_path)
            
            # 提取备份时间
            try:
                # 从文件名中提取时间，格式如 booknav_20250414193523.db3
                time_str = filename.split('_')[1].split('.')[0]
                backup_time = datetime.strptime(time_str, '%Y%m%d%H%M%S')
                time_display = backup_time.strftime('%Y-%m-%d %H:%M:%S')
            except:
                time_display = datetime.fromtimestamp(file_stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            
            backups.append({
                'filename': filename,
                'size': file_stats.st_size,
                'size_display': format_file_size(file_stats.st_size),
                'time': file_stats.st_mtime,
                'time_display': time_display
            })
    
    # 按时间降序排序
    backups.sort(key=lambda x: x['time'], reverse=True)
    
    return render_template('admin/backup_list.html', title='备份管理', backups=backups)

@bp.route('/download-backup/<filename>')
@login_required
@superadmin_required
def download_backup(filename):
    """下载备份文件"""
    # 安全检查，确保文件名不包含路径分隔符
    if os.path.sep in filename or '..' in filename:
        abort(404)
    
    backup_dir = os.path.join(current_app.root_path, 'backups')
    backup_path = os.path.join(backup_dir, filename)
    
    # 检查文件是否存在
    if not os.path.exists(backup_path):
        flash('备份文件不存在', 'danger')
        return redirect(url_for('admin.backup_list'))
    
    try:
        return send_file(
            backup_path,
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        current_app.logger.error(f"下载备份文件失败: {str(e)}")
        flash(f'下载失败: {str(e)}', 'danger')
        return redirect(url_for('admin.backup_list'))

@bp.route('/delete-backup/<filename>', methods=['POST'])
@login_required
@superadmin_required
def delete_backup(filename):
    """删除备份文件"""
    # 安全检查，确保文件名不包含路径分隔符
    if os.path.sep in filename or '..' in filename:
        abort(404)
    
    backup_dir = os.path.join(current_app.root_path, 'backups')
    backup_path = os.path.join(backup_dir, filename)
    
    # 检查文件是否存在
    if not os.path.exists(backup_path):
        flash('备份文件不存在', 'danger')
        return redirect(url_for('admin.backup_list'))
    
    try:
        os.remove(backup_path)
        flash('备份文件已删除', 'success')
    except Exception as e:
        current_app.logger.error(f"删除备份文件失败: {str(e)}")
        flash(f'删除失败: {str(e)}', 'danger')
    
    return redirect(url_for('admin.backup_list'))

@bp.route('/restore-backup/<filename>', methods=['POST'])
@login_required
@superadmin_required
def restore_backup(filename):
    """恢复备份"""
    # 安全检查，确保文件名不包含路径分隔符
    if os.path.sep in filename or '..' in filename:
        abort(404)
    
    backup_dir = os.path.join(current_app.root_path, 'backups')
    backup_path = os.path.join(backup_dir, filename)
    
    # 检查文件是否存在
    if not os.path.exists(backup_path):
        flash('备份文件不存在', 'danger')
        return redirect(url_for('admin.backup_list'))
    
    try:
        # 目标数据库路径
        db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        
        # 数据库路径可能是相对路径，需要转换为绝对路径
        if not os.path.isabs(db_path):
            db_path = os.path.join(current_app.root_path, db_path)
        
        # 关闭数据库连接
        db.session.close()
        db.engine.dispose()
        
        # 先创建当前数据库的临时备份
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        temp_backup = f"{db_path}.restore_bak.{timestamp}"
        shutil.copy2(db_path, temp_backup)
        
        # 恢复备份
        shutil.copy2(backup_path, db_path)
        
        flash('数据库恢复成功，请重新登录', 'success')
        # 恢复后需要重新登录
        return redirect(url_for('auth.logout'))
    except Exception as e:
        current_app.logger.error(f"恢复备份失败: {str(e)}")
        flash(f'恢复失败: {str(e)}', 'danger')
        return redirect(url_for('admin.backup_list'))

def format_file_size(size_bytes):
    """格式化文件大小显示"""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"

# 批量抓取缺失图标相关功能
from app.main.utils import get_website_icon
import time
import json
import threading
from queue import Queue

# 用于存储批量处理的状态
icon_fetch_status = {
    'is_running': False,
    'total': 0,
    'processed': 0,
    'success': 0,
    'failed': 0,
    'start_time': None
}

# 创建队列和线程事件，用于控制抓取过程
icon_fetch_queue = Queue()
icon_fetch_stop_event = threading.Event()

@bp.route('/api/batch-fetch-icons', methods=['POST'])
@login_required
@superadmin_required
def batch_fetch_icons():
    """开始批量抓取缺失的图标"""
    global icon_fetch_status
    
    # 检查是否已有正在进行的批量处理
    if icon_fetch_status['is_running']:
        return jsonify({
            'success': False,
            'message': '已有批量抓取任务正在运行，请等待其完成'
        })
    
    # 重置状态
    icon_fetch_status = {
        'is_running': True,
        'total': 0,
        'processed': 0,
        'success': 0,
        'failed': 0,
        'start_time': time.time()
    }
    
    # 清除可能的停止标志
    icon_fetch_stop_event.clear()
    
    # 获取当前应用实例传递给线程
    app = current_app._get_current_object()  # 获取真实的应用对象
    
    # 启动后台线程处理
    threading.Thread(target=process_missing_icons, args=(app,)).start()
    
    return jsonify({
        'success': True,
        'message': '批量抓取图标任务已启动'
    })

@bp.route('/api/batch-fetch-icons/status')
@login_required
@superadmin_required
def batch_fetch_icons_status():
    """获取批量抓取过程的状态"""
    global icon_fetch_status
    
    # 计算执行时间
    elapsed_time = ""
    if icon_fetch_status['start_time']:
        elapsed_seconds = int(time.time() - icon_fetch_status['start_time'])
        minutes, seconds = divmod(elapsed_seconds, 60)
        elapsed_time = f"{minutes}分{seconds}秒"
    
    response = jsonify({
        'is_running': icon_fetch_status['is_running'],
        'total': icon_fetch_status['total'],
        'processed': icon_fetch_status['processed'],
        'success': icon_fetch_status['success'],
        'failed': icon_fetch_status['failed'],
        'elapsed_time': elapsed_time,
        'percent': 0 if icon_fetch_status['total'] == 0 else int((icon_fetch_status['processed'] / icon_fetch_status['total']) * 100)
    })
    
    # 添加禁用缓冲的头部，解决Docker环境中显示问题
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    
    return response

@bp.route('/api/batch-fetch-icons/stop', methods=['POST'])
@login_required
@superadmin_required
def batch_fetch_icons_stop():
    """停止批量抓取图标任务"""
    global icon_fetch_status
    
    if not icon_fetch_status['is_running']:
        return jsonify({
            'success': False,
            'message': '没有正在运行的抓取任务'
        })
    
    # 设置停止标志
    icon_fetch_stop_event.set()
    
    return jsonify({
        'success': True,
        'message': '已发送停止信号，任务将在当前图标处理完成后停止'
    })

def process_missing_icons(app):
    """后台处理所有缺失图标的网站"""
    global icon_fetch_status
    
    # 在线程中使用应用上下文
    with app.app_context():
        try:
            # 查询所有缺失图标的网站
            missing_icon_websites = Website.query.filter(
                (Website.icon.is_(None)) | 
                (Website.icon == '') | 
                (Website.icon.like('%cccyun.cc%'))  # 包含备用图标的网站
            ).all()
            
            # 更新总数
            icon_fetch_status['total'] = len(missing_icon_websites)
            
            # 记录日志
            current_app.logger.info(f"开始批量抓取图标，共{len(missing_icon_websites)}个网站")
            
            # 处理每个网站
            for website in missing_icon_websites:
                # 检查是否收到了停止信号
                if icon_fetch_stop_event.is_set():
                    current_app.logger.info("收到停止信号，中断批量抓取")
                    break
                    
                try:
                    # 抓取图标
                    result = get_website_icon(website.url)
                    
                    # 根据结果更新网站图标
                    if result["success"] and result.get("icon_url"):
                        # 使用API返回的图标URL
                        website.icon = result["icon_url"]
                        icon_fetch_status['success'] += 1
                    elif "fallback_url" in result and result["fallback_url"]:
                        # 验证备用图标URL是否可访问
                        fallback_url = result["fallback_url"]
                        try:
                            # 使用HEAD请求快速验证URL可访问性
                            head_response = requests.head(
                                fallback_url, 
                                timeout=5, 
                                headers={'User-Agent': 'Mozilla/5.0'}
                            )
                            if head_response.status_code < 400:  # 2xx或3xx状态码表示可访问
                                # 备用URL可访问，设置图标并算作成功
                                website.icon = fallback_url
                                icon_fetch_status['success'] += 1
                            else:
                                # 备用URL返回错误状态码，算作失败
                                icon_fetch_status['failed'] += 1
                        except Exception as url_err:
                            # 请求过程中出错，算作失败
                            current_app.logger.warning(f"验证备用图标URL失败: {fallback_url} - {str(url_err)}")
                            icon_fetch_status['failed'] += 1
                    else:
                        # 完全无法获取图标才算失败
                        icon_fetch_status['failed'] += 1
                    
                    # 更新处理数量
                    icon_fetch_status['processed'] += 1
                    
                    # 每10个网站提交一次，避免长事务
                    if icon_fetch_status['processed'] % 10 == 0:
                        db.session.commit()
                        
                    # 适当休眠，避免API限制
                    time.sleep(1)
                    
                except Exception as e:
                    # 处理异常，尝试使用备用图标
                    try:
                        from urllib.parse import urlparse
                        parsed_url = urlparse(website.url)
                        domain = parsed_url.netloc
                        fallback_url = f"https://favicon.cccyun.cc/{domain}"
                        
                        # 验证备用图标URL是否可访问
                        try:
                            # 使用HEAD请求快速验证URL可访问性
                            head_response = requests.head(
                                fallback_url, 
                                timeout=5, 
                                headers={'User-Agent': 'Mozilla/5.0'}
                            )
                            if head_response.status_code < 400:  # 2xx或3xx状态码表示可访问
                                # 备用URL可访问，设置图标并算作成功
                                website.icon = fallback_url
                                icon_fetch_status['success'] += 1
                            else:
                                # 备用URL返回错误状态码，算作失败
                                icon_fetch_status['failed'] += 1
                        except Exception as url_err:
                            # 请求过程中出错，算作失败
                            current_app.logger.warning(f"验证备用图标URL失败: {fallback_url} - {str(url_err)}")
                            icon_fetch_status['failed'] += 1
                    except:
                        # 完全无法设置图标才算失败
                        icon_fetch_status['failed'] += 1
                    
                    icon_fetch_status['processed'] += 1
                    current_app.logger.error(f"抓取图标出错 ({website.url}): {str(e)}")
            
            # 提交所有更改
            db.session.commit()
            current_app.logger.info(f"批量抓取图标完成，成功: {icon_fetch_status['success']}，失败: {icon_fetch_status['failed']}")
            
        except Exception as e:
            current_app.logger.error(f"批量抓取图标任务出错: {str(e)}")
        finally:
            # 更新状态为已完成
            icon_fetch_status['is_running'] = False

def is_project_db(db_path):
    """检查是否为本项目数据库格式"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查是否包含项目特有的表结构
        required_tables = ['category', 'website', 'user']
        for table in required_tables:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if not cursor.fetchone():
                conn.close()
                return False
        
        # 检查category表是否有color字段（本项目特有）
        cursor.execute("PRAGMA table_info(category)")
        columns = cursor.fetchall()
        has_color = False
        for column in columns:
            if column[1] == 'color':
                has_color = True
                break
        
        conn.close()
        return has_color
    except Exception as e:
        current_app.logger.error(f"检查项目数据库格式失败: {str(e)}")
        return False

def import_project_db(db_path, import_type, admin_id):
    """导入本项目格式的数据库"""
    try:
        # 备份现有数据库
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        backup_filename = f"pre_import_backup_{timestamp}.db3"
        backup_dir = os.path.join(current_app.root_path, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, backup_filename)
        
        # 复制当前数据库作为备份
        db_path_current = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if not os.path.isabs(db_path_current):
            db_path_current = os.path.join(current_app.root_path, db_path_current)
        shutil.copy2(db_path_current, backup_path)
        
        # 如果是替换模式，直接使用导入的数据库替换现有数据库
        if import_type == "replace":
            current_app.logger.info("执行替换模式，直接替换数据库文件")
            shutil.copy2(db_path, db_path_current)
            
            # 重新连接数据库（强制SQLAlchemy重新加载数据）
            db.session.remove()
            db.engine.dispose()
            
            # 获取数据库统计信息
            conn = sqlite3.connect(db_path_current)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM category")
            cat_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM website")
            link_count = cursor.fetchone()[0]
            conn.close()
            
            return True, cat_count, link_count
        else:
            # 合并模式：保留现有数据，添加新数据
            current_app.logger.info("执行合并模式，从导入的数据库添加数据")
            
            # 连接源数据库
            source_conn = sqlite3.connect(db_path)
            source_cursor = source_conn.cursor()
            
            # 获取分类数据
            source_cursor.execute("SELECT id, name, description, icon, color, \"order\", parent_id FROM category")
            categories = source_cursor.fetchall()
            
            # 获取网站数据
            source_cursor.execute("""
                SELECT id, title, url, description, icon, views, is_featured, sort_order, 
                       category_id, is_private 
                FROM website
            """)
            websites = source_cursor.fetchall()
            
            # 关闭源数据库连接
            source_conn.close()
            
            # 导入分类
            cat_id_mapping = {}  # 旧ID到新ID的映射
            existing_categories = {c.name.lower(): c for c in Category.query.all()}
            
            for cat in categories:
                cat_id, name, desc, icon, color, order, parent_id = cat
                
                # 检查是否已存在同名分类
                cat_name = name.lower() if name else ""
                if cat_name in existing_categories:
                    # 使用现有分类ID
                    cat_id_mapping[cat_id] = existing_categories[cat_name].id
                else:
                    # 创建新分类
                    new_category = Category(
                        name=name,
                        description=desc or "",
                        icon=icon or "folder",
                        color=color or "#3498db",
                        order=order or 0
                    )
                    db.session.add(new_category)
                    db.session.flush()  # 获取新ID
                    
                    # 保存ID映射关系
                    cat_id_mapping[cat_id] = new_category.id
                    existing_categories[cat_name] = new_category
            
            db.session.commit()
            
            # 更新父子关系
            for cat in categories:
                cat_id, _, _, _, _, _, parent_id = cat
                if parent_id and parent_id in cat_id_mapping and cat_id in cat_id_mapping:
                    child = Category.query.get(cat_id_mapping[cat_id])
                    if child:
                        child.parent_id = cat_id_mapping[parent_id]
            
            db.session.commit()
            
            # 导入网站数据
            existing_urls = {w.url.lower(): True for w in Website.query.all()}
            imported_count = 0
            
            for site in websites:
                site_id, title, url, desc, icon, views, is_featured, sort_order, category_id, is_private = site
                
                # 检查URL是否已存在
                url_lower = url.lower() if url else ""
                if url_lower and url_lower in existing_urls:
                    continue
                
                # 确定新的分类ID
                new_cat_id = None
                if category_id and category_id in cat_id_mapping:
                    new_cat_id = cat_id_mapping[category_id]
                
                # 创建新网站
                new_website = Website(
                    title=title,
                    url=url,
                    description=desc or "",
                    icon=icon,
                    views=views or 0,
                    is_featured=bool(is_featured),
                    sort_order=sort_order or 0,
                    category_id=new_cat_id,
                    created_by_id=admin_id,
                    is_private=bool(is_private),
                    created_at=datetime.now()
                )
                db.session.add(new_website)
                imported_count += 1
                
                # 每100条提交一次，避免内存问题
                if imported_count % 100 == 0:
                    db.session.commit()
            
            db.session.commit()
            return True, len(cat_id_mapping), imported_count
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"导入本项目数据库失败: {str(e)}")
        raise e

@bp.route('/wallpaper', methods=['GET', 'POST'])
@login_required
@admin_required
def wallpaper():
    """背景管理页面"""
    form = BackgroundForm()
    
    if form.validate_on_submit():
        background = Background(
            title=form.title.data,
            type=form.type.data,
            device_type=form.device_type.data,
            created_by_id=current_user.id
        )
        
        # 处理图片上传
        if form.background_file.data and form.type.data == 'image':
            bg_filename = save_image(form.background_file.data, 'backgrounds')
            if bg_filename:
                background.url = url_for('static', filename=f'uploads/backgrounds/{bg_filename}')
        elif form.url.data:
            background.url = form.url.data
        
        db.session.add(background)
        db.session.commit()
        flash('背景添加成功', 'success')
        return redirect(url_for('admin.wallpaper'))
    
    backgrounds = Background.query.order_by(Background.created_at.desc()).all()
    return render_template('admin/wallpaper.html', title='背景管理', form=form, backgrounds=backgrounds)


@bp.route('/apply-background', methods=['POST'])
@login_required
@admin_required
def apply_background():
    """应用背景"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效的请求数据'})
    
    bg_id = data.get('id')
    bg_type = data.get('type')
    bg_url = data.get('url')
    device_type = data.get('device_type')
    
    if not all([bg_type, bg_url, device_type]):
        return jsonify({'success': False, 'message': '缺少必要参数'})
    
    try:
        settings = SiteSettings.get_settings()
        if device_type == 'pc':
            settings.pc_background_type = bg_type
            settings.pc_background_url = bg_url
        elif device_type == 'mobile':
            settings.mobile_background_type = bg_type
            settings.mobile_background_url = bg_url
        elif device_type == 'both':
            settings.pc_background_type = bg_type
            settings.pc_background_url = bg_url
            settings.mobile_background_type = bg_type
            settings.mobile_background_url = bg_url
        else:
            return jsonify({'success': False, 'message': '未知的设备类型'})
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@bp.route('/delete-background/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_background(id):
    """删除背景"""
    background = Background.query.get_or_404(id)
    
    # 检查权限（只有超级管理员或创建者可以删除）
    if not current_user.is_superadmin and background.created_by_id != current_user.id:
        return jsonify({'success': False, 'message': '没有权限删除此背景'})
    
    try:
        # 保存图片URL用于后续删除文件
        bg_url = background.url
        
        # 如果当前正在使用这个背景，则重置默认背景
        settings = SiteSettings.get_settings()
        if settings.background_url == background.url:
            settings.background_type = 'none'
            settings.background_url = None
        
        # 从数据库中删除记录
        db.session.delete(background)
        db.session.commit()
        
        # 删除物理文件（仅针对上传的图片，不删除外部URL）
        if bg_url and '/static/uploads/backgrounds/' in bg_url:
            try:
                # 从URL中提取文件名
                file_path = bg_url.split('/static/')[1]
                full_path = os.path.join(current_app.root_path, 'static', file_path)
                
                # 检查文件是否存在，如果存在则删除
                if os.path.exists(full_path):
                    os.remove(full_path)
                    current_app.logger.info(f'已删除背景图片文件: {full_path}')
            except Exception as e:
                current_app.logger.error(f'删除背景图片文件失败: {str(e)}')
                # 文件删除失败不影响整体操作，继续返回成功
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@bp.route('/clear-background', methods=['POST'])
@login_required
@admin_required
def clear_background():
    """清除背景图片设置"""
    try:
        # 获取站点设置
        settings = SiteSettings.get_settings()
        # 清除背景相关设置
        settings.background_type = 'none'
        settings.background_url = None
        # 保存更改
        db.session.commit()
        return jsonify({'success': True, 'message': '背景设置已清除'})
    except Exception as e:
        current_app.logger.error(f"清除背景设置失败: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'操作失败: {str(e)}'})

# ---------------- 死链检测相关功能 ----------------

import uuid
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import csv
import io

# 全局变量，用于跟踪死链检测任务状态
deadlink_check_task = {
    'is_running': False,
    'should_stop': False,
    'processed': 0,
    'valid': 0,
    'invalid': 0,
    'total': 0,
    'start_time': None,
    'end_time': None,
    'check_id': None,  # 添加check_id字段
    'result_queue': queue.Queue()
}

@bp.route('/batch-check-deadlinks', methods=['POST'])
@login_required
@superadmin_required
def batch_check_deadlinks():
    """启动批量死链检测任务"""
    global deadlink_check_task
    
    # 检查是否有任务正在运行
    if deadlink_check_task['is_running']:
        return jsonify({
            'success': False,
            'message': '已有死链检测任务正在运行'
        })
    
    # 清空历史检测记录
    try:
        # 删除所有历史死链检测记录
        DeadlinkCheck.query.delete()
        db.session.commit()
        current_app.logger.info('已清空所有历史死链检测记录')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'清空历史检测记录失败: {str(e)}')
        return jsonify({
            'success': False,
            'message': f'清空历史检测记录失败: {str(e)}'
        })
    
    # 重置任务状态
    deadlink_check_task.update({
        'is_running': True,
        'should_stop': False,
        'processed': 0,
        'valid': 0,
        'invalid': 0,
        'total': 0,
        'start_time': time.time(),
        'end_time': None,
        'check_id': str(uuid.uuid4()),  # 确保生成一个新的check_id
        'result_queue': queue.Queue()
    })
    
    # 启动后台任务
    threading.Thread(target=process_deadlink_check, args=(current_app._get_current_object(),), daemon=True).start()
    
    return jsonify({
        'success': True,
        'message': '死链检测任务已启动'
    })

@bp.route('/batch-check-deadlinks/status')
@login_required
@superadmin_required
def batch_check_deadlinks_status():
    """获取死链检测任务状态"""
    global deadlink_check_task
    
    # 计算进度百分比
    percent = 0
    if deadlink_check_task['total'] > 0:
        percent = round((deadlink_check_task['processed'] / deadlink_check_task['total']) * 100)
    
    # 计算已用时间
    elapsed_time = 0
    if deadlink_check_task['start_time']:
        if deadlink_check_task['end_time']:
            elapsed_time = round(deadlink_check_task['end_time'] - deadlink_check_task['start_time'])
        else:
            elapsed_time = round(time.time() - deadlink_check_task['start_time'])
    
    # 格式化时间
    elapsed_time_str = format_elapsed_time(elapsed_time)
    
    response = jsonify({
        'is_running': deadlink_check_task['is_running'],
        'processed': deadlink_check_task['processed'],
        'valid': deadlink_check_task['valid'],
        'invalid': deadlink_check_task['invalid'],
        'total': deadlink_check_task['total'],
        'elapsed_time': elapsed_time_str,
        'check_id': deadlink_check_task.get('check_id', ''),  # 添加check_id
        'percent': percent  # 确保百分比存在
    })
    
    # 添加禁用缓冲的头部，解决Docker环境中显示问题
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    
    return response

@bp.route('/batch-check-deadlinks/stop', methods=['POST'])
@login_required
@superadmin_required
def batch_check_deadlinks_stop():
    """停止死链检测任务"""
    global deadlink_check_task
    
    if not deadlink_check_task['is_running']:
        return jsonify({
            'success': False,
            'message': '没有正在运行的死链检测任务'
        })
    
    # 设置停止标志
    deadlink_check_task['should_stop'] = True
    
    return jsonify({
        'success': True,
        'message': '已发送停止信号，任务将在当前链接检查完成后停止'
    })

def process_check_results(app):
    """处理链接检测结果的函数"""
    global deadlink_check_task
    
    with app.app_context():
        while True:
            try:
                # 从队列中获取一个结果，最多等待1秒
                result = deadlink_check_task['result_queue'].get(timeout=1)
                
                # 检查是否为结束信号
                if result is None:
                    app.logger.info("结果处理完成！")
                    break
                
                # 解析结果
                website_id, url, is_valid, status_code, error_type, error_message, response_time = result
                
                # 更新统计信息
                deadlink_check_task['processed'] += 1
                if is_valid:
                    deadlink_check_task['valid'] += 1
                else:
                    deadlink_check_task['invalid'] += 1
                
                # 更新数据库
                try:
                    # 确保check_id存在
                    if not deadlink_check_task.get('check_id'):
                        app.logger.error("缺少check_id，无法保存结果")
                        continue
                        
                    # 查找对应的Website记录
                    website = Website.query.get(website_id)
                    if website:
                        # 创建新的检测结果记录
                        check_result = DeadlinkCheck(
                            check_id=deadlink_check_task['check_id'],
                            website_id=website_id,
                            url=url,
                            is_valid=is_valid,
                            status_code=status_code,
                            error_type=error_type,
                            error_message=error_message,
                            response_time=response_time,
                            checked_at=datetime.utcnow()
                        )
                        
                        # 更新Website的last_check和is_valid字段
                        website.last_check = datetime.utcnow()
                        website.is_valid = is_valid
                        
                        # 保存到数据库
                        db.session.add(check_result)
                        db.session.commit()
                    else:
                        app.logger.warning(f"处理结果时找不到Website ID: {website_id}")
                
                except Exception as e:
                    app.logger.error(f"保存检测结果时出错: {str(e)}")
                    # 尝试回滚事务
                    try:
                        db.session.rollback()
                    except:
                        pass
                
                # 标记队列任务已完成
                deadlink_check_task['result_queue'].task_done()
                
                # 记录进度
                if deadlink_check_task['processed'] % 10 == 0 or deadlink_check_task['processed'] == deadlink_check_task['total']:
                    app.logger.info(f"已处理 {deadlink_check_task['processed']}/{deadlink_check_task['total']} 个链接 "
                                  f"({deadlink_check_task['processed']/deadlink_check_task['total']*100:.1f}%)")
            
            except queue.Empty:
                # 队列为空，检查任务是否已完成
                if deadlink_check_task['processed'] >= deadlink_check_task['total'] or deadlink_check_task['should_stop']:
                    # 如果已经处理完所有链接或收到停止信号，则结束处理
                    app.logger.info("没有更多结果需要处理，结束结果处理线程")
                    break
                    
                # 否则继续等待新的结果
                continue
                
            except Exception as e:
                app.logger.error(f"处理结果时发生错误: {str(e)}")

def process_deadlink_check(app):
    """执行死链检测的后台任务"""
    global deadlink_check_task
    
    with app.app_context():
        try:
            # 启动结果处理线程
            result_processor = threading.Thread(
                target=process_check_results,
                args=(app,),
                daemon=True
            )
            result_processor.start()
            
            # 获取所有需要检测的网站链接
            websites = Website.query.all()
            total_websites = len(websites)
            deadlink_check_task['total'] = total_websites
            
            app.logger.info(f"开始死链检测，共有 {total_websites} 个链接需要检测")
            
            # 使用线程池进行并行检测
            max_workers = min(5, total_websites)  # 降低线程数，减少资源占用
            
            # 对链接进行分批处理，避免处理过多链接导致内存问题
            batch_size = 20  # 减小批次大小
            
            for i in range(0, total_websites, batch_size):
                # 检查是否应该停止
                if deadlink_check_task['should_stop']:
                    app.logger.info("收到停止信号，终止死链检测任务...")
                    break
                
                batch = websites[i:i+batch_size]
                app.logger.info(f"处理批次 {i//batch_size + 1}/{(total_websites+batch_size-1)//batch_size}，包含 {len(batch)} 个链接")
                
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # 提交批次任务
                    future_to_website = {
                        executor.submit(check_single_link_thread_safe, website): website
                        for website in batch
                    }
                    
                    # 等待所有任务完成
                    for future in as_completed(future_to_website):
                        # 检查是否应该停止
                        if deadlink_check_task['should_stop']:
                            app.logger.info("收到停止信号，正在终止当前批次...")
                            # 取消所有未完成的任务
                            for f in future_to_website:
                                if not f.done():
                                    f.cancel()
                            break
                
                # 每批次处理完成后，短暂休息以减轻系统负担
                time.sleep(1)
            
            # 任务完成
            deadlink_check_task['end_time'] = time.time()
            elapsed_seconds = int(deadlink_check_task['end_time'] - deadlink_check_task['start_time'])
            app.logger.info(f"死链检测完成！共检测 {deadlink_check_task['processed']} 个链接，"
                           f"有效 {deadlink_check_task['valid']} 个，"
                           f"无效 {deadlink_check_task['invalid']} 个，"
                           f"用时 {format_elapsed_time(elapsed_seconds)}")
            
            # 等待结果处理线程完成
            deadlink_check_task['result_queue'].put(None)  # 发送结束信号
            result_processor.join(timeout=30)  # 最多等待30秒
            
        except Exception as e:
            app.logger.error(f"死链检测任务发生错误: {str(e)}")
        finally:
            deadlink_check_task['is_running'] = False
            if not deadlink_check_task['end_time']:
                deadlink_check_task['end_time'] = time.time()

def check_single_link_thread_safe(website):
    """线程安全的链接检测函数，不直接操作数据库"""
    global deadlink_check_task
    
    url = website.url
    is_valid = False
    status_code = None
    error_type = None
    error_message = None
    start_time = time.time()
    
    # 确保URL有效
    if not url or not (url.startswith('http://') or url.startswith('https://')):
        error_type = 'invalid_url'
        error_message = 'URL格式无效'
        response_time = 0
        
        # 将结果放入队列
        result = (website.id, url, is_valid, status_code, error_type, error_message, response_time)
        deadlink_check_task['result_queue'].put(result)
        return is_valid
    
    try:
        # 发送HTTP请求检查链接
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache'
        }
        
        # 尝试HEAD请求，更轻量和快速
        try:
            response = requests.head(url, timeout=15, headers=headers, allow_redirects=True, verify=False)
            status_code = response.status_code
            
            # 有些网站可能不支持HEAD请求，如果得到4xx或5xx状态码，尝试GET请求
            if status_code >= 400:
                raise requests.exceptions.RequestException("HEAD请求失败，尝试GET请求")
                
        except requests.exceptions.RequestException:
            # 尝试GET请求，但只获取头部内容以节省带宽
            response = requests.get(url, timeout=15, headers=headers, allow_redirects=True, stream=True, verify=False)
            # 只读取少量内容
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:  # 过滤掉保持活动的新行
                    break
            status_code = response.status_code
            response.close()
        
        # 2xx和3xx状态码通常表示链接有效
        # 某些特殊的4xx状态码也可能表示网站正常工作，只是访问受限
        is_valid = (200 <= status_code < 400) or status_code in [401, 403]
        
        if not is_valid:
            error_type = f'http_{status_code}'
            error_message = f'HTTP状态码: {status_code}'
            
    except requests.exceptions.Timeout:
        error_type = 'timeout'
        error_message = '请求超时'
    except requests.exceptions.SSLError:
        error_type = 'ssl_error'
        error_message = 'SSL证书验证失败'
    except requests.exceptions.ConnectionError:
        error_type = 'connection_error'
        error_message = '连接错误'
    except requests.exceptions.TooManyRedirects:
        error_type = 'too_many_redirects'
        error_message = '重定向次数过多'
    except requests.exceptions.RequestException as e:
        error_type = 'request_error'
        error_message = str(e)
    except Exception as e:
        error_type = 'unknown_error'
        error_message = str(e)
    
    # 计算响应时间
    response_time = time.time() - start_time
    
    # 将结果放入队列，而不是直接操作数据库
    result = (website.id, url, is_valid, status_code, error_type, error_message, response_time)
    deadlink_check_task['result_queue'].put(result)
    
    return is_valid

@bp.route('/deadlink-results')
@login_required
@superadmin_required
def deadlink_results():
    """显示死链检测结果页面"""
    global deadlink_check_task
    
    # 使用全局check_id
    check_id = deadlink_check_task.get('check_id')
    if not check_id:
        # 如果没有最新的检测ID，尝试从数据库获取最新的检测批次ID
        latest_check = DeadlinkCheck.query.order_by(DeadlinkCheck.checked_at.desc()).first()
        if latest_check:
            check_id = latest_check.check_id
        else:
            flash('没有找到检测记录', 'warning')
            return redirect(url_for('admin.data_management'))
    
    # 获取统计信息
    total = DeadlinkCheck.query.filter_by(check_id=check_id).count()
    valid = DeadlinkCheck.query.filter_by(check_id=check_id, is_valid=True).count()
    invalid = DeadlinkCheck.query.filter_by(check_id=check_id, is_valid=False).count()
    
    # 计算检测时间
    first_check = DeadlinkCheck.query.filter_by(check_id=check_id).order_by(DeadlinkCheck.checked_at).first()
    last_check = DeadlinkCheck.query.filter_by(check_id=check_id).order_by(DeadlinkCheck.checked_at.desc()).first()
    
    elapsed_time = 0
    if first_check and last_check:
        elapsed_time = (last_check.checked_at - first_check.checked_at).total_seconds()
        elapsed_time = round(elapsed_time)
    
    # 获取所有无效链接
    invalid_links = []
    invalid_checks = DeadlinkCheck.query.filter_by(check_id=check_id, is_valid=False).all()
    
    for check in invalid_checks:
        # 获取网站和分类信息
        website = Website.query.get(check.website_id)
        if website:
            category_name = website.category.name if website.category else '未分类'
            invalid_links.append({
                'id': website.id,
                'title': website.title,
                'url': website.url,
                'icon': website.icon,
                'category_name': category_name,
                'error_type': check.error_type or '未知错误',
                'error_message': check.error_message or '无错误信息'
            })
    
    # 准备统计数据
    stats = {
        'total': total,
        'valid': valid,
        'invalid': invalid,
        'elapsed_time': elapsed_time
    }
    
    return render_template('admin/deadlink_results.html',
                           title='死链检测结果',
                           stats=stats,
                           invalid_links=invalid_links)

@bp.route('/export-deadlink-results')
@login_required
@superadmin_required
def export_deadlink_results():
    """导出死链检测结果为CSV文件"""
    global deadlink_check_task
    
    # 使用全局check_id
    check_id = deadlink_check_task.get('check_id')
    if not check_id:
        # 如果没有最新的检测ID，尝试从数据库获取最新的检测批次ID
        latest_check = DeadlinkCheck.query.order_by(DeadlinkCheck.checked_at.desc()).first()
        if latest_check:
            check_id = latest_check.check_id
        else:
            flash('没有找到检测记录', 'warning')
            return redirect(url_for('admin.deadlink_results'))
    
    # 准备CSV数据
    output = io.StringIO()
    # 添加BOM标记以解决Excel中文乱码问题
    output.write('\ufeff')
    writer = csv.writer(output)
    
    # 写入CSV头
    writer.writerow(['ID', '网站名称', 'URL', '所属分类', '状态', '错误类型', '错误信息', '响应时间(秒)', '检测时间'])
    
    # 查询所有检测结果
    checks = DeadlinkCheck.query.filter_by(check_id=check_id).all()
    
    for check in checks:
        website = Website.query.get(check.website_id)
        if website:
            category_name = website.category.name if website.category else '未分类'
            status = '有效' if check.is_valid else '无效'
            writer.writerow([
                website.id,
                website.title,
                website.url,
                category_name,
                status,
                check.error_type or '',
                check.error_message or '',
                f"{check.response_time:.2f}" if check.response_time else '',
                check.checked_at.strftime('%Y-%m-%d %H:%M:%S')
            ])
    
    # 设置响应头
    timestamp = datetime.now().strftime('%Y%m%d%H%M')
    output.seek(0)
    
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment;filename=deadlink_results_{timestamp}.csv",
            "Content-Type": "text/csv; charset=utf-8"
        }
    )

@bp.route('/delete-deadlinks', methods=['POST'])
@login_required
@superadmin_required
def delete_deadlinks():
    """删除选中的死链接"""
    data = request.json
    link_ids = data.get('link_ids', [])
    
    if not link_ids:
        return jsonify({
            'success': False,
            'message': '未选择要删除的链接'
        })
    
    try:
        # 获取要删除的网站
        websites = Website.query.filter(Website.id.in_(link_ids)).all()
        
        # 记录删除的网站信息（用于操作日志）
        for website in websites:
            # 创建操作日志
            log = OperationLog(
                user_id=current_user.id,
                operation_type='DELETE',
                website_id=website.id,
                website_title=website.title,
                website_url=website.url,
                website_icon=website.icon,
                category_id=website.category_id,
                category_name=website.category.name if website.category else '未分类',
                details=json.dumps({
                    'source': 'deadlink_check',
                    'delete_reason': '死链检测'
                })
            )
            db.session.add(log)
        
        # 删除网站
        delete_count = Website.query.filter(Website.id.in_(link_ids)).delete(synchronize_session=False)
        
        # 提交事务
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'已成功删除 {delete_count} 个无效链接'
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除死链接失败: {str(e)}")
        
        return jsonify({
            'success': False,
            'message': f'删除失败: {str(e)}'
        })

def format_elapsed_time(seconds):
    """格式化耗时"""
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        minutes = seconds // 60
        seconds %= 60
        return f"{minutes}分{seconds}秒"
    else:
        hours = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60
        return f"{hours}时{minutes}分{seconds}秒"

@bp.route('/clear-deadlink-records', methods=['POST'])
@login_required
@superadmin_required
def clear_deadlink_records():
    """手动清理所有死链检测记录"""
    global deadlink_check_task
    
    # 检查是否有任务正在运行
    if deadlink_check_task['is_running']:
        return jsonify({
            'success': False,
            'message': '当前有死链检测任务正在运行，无法清理记录'
        })
    
    try:
        # 删除所有历史死链检测记录
        count = DeadlinkCheck.query.count()
        DeadlinkCheck.query.delete()
        db.session.commit()
        current_app.logger.info(f'已手动清空所有历史死链检测记录，共 {count} 条')
        
        # 重置检测ID
        deadlink_check_task['check_id'] = None
        
        return jsonify({
            'success': True,
            'message': f'已成功清理 {count} 条历史检测记录'
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'清空历史检测记录失败: {str(e)}')
        return jsonify({
            'success': False,
            'message': f'清空历史检测记录失败: {str(e)}'
        })
