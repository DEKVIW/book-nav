#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用户管理路由"""

import os
from datetime import datetime
from flask import render_template, redirect, url_for, flash, request, url_for as flask_url_for
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app import db
from app.admin import bp
from app.admin.forms import UserEditForm
from app.admin.decorators import admin_required, superadmin_required
from app.models import User, Website, OperationLog


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
    from flask import current_app
    
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
                user.avatar = flask_url_for('static', filename=f'uploads/avatars/{avatar_filename}')
                flash('头像已更新', 'success')
            except Exception as e:
                flash(f'头像上传失败: {str(e)}', 'danger')
        
        db.session.commit()
        flash('用户信息更新成功!', 'success')
        return redirect(url_for('admin.users'))
    
    return render_template('admin/user_edit.html', title='编辑用户', form=form, user=user)


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

