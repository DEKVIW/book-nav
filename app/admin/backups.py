#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""备份管理路由"""

import os
import shutil
from datetime import datetime
from flask import render_template, redirect, url_for, flash, send_file, abort, current_app
from flask_login import login_required
from app import db
from app.admin import bp
from app.admin.decorators import superadmin_required


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

