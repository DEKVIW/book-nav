#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""背景管理路由"""

import os
from flask import render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from app import db
from app.admin import bp
from app.admin.forms import BackgroundForm
from app.admin.decorators import admin_required
from app.admin.utils import save_image
from app.models import Background, SiteSettings


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

