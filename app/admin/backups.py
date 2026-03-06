#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""备份管理路由（本地备份 + WebDAV 云端备份）"""

import os
import shutil
import threading
import zipfile
from datetime import datetime
from flask import (
    render_template, redirect, url_for, flash, send_file,
    abort, current_app, request, jsonify
)
from flask_login import login_required
from app import db
from app.admin import bp
from app.admin.decorators import superadmin_required
from app.models import SiteSettings


# ==================== 自动备份线程管理 ====================

_auto_backup_thread = None
_auto_backup_stop = threading.Event()
_backup_lock = threading.Lock()


def start_auto_backup(app):
    """启动自动备份后台线程"""
    global _auto_backup_thread
    
    if _auto_backup_thread and _auto_backup_thread.is_alive():
        return
    
    _auto_backup_stop.clear()
    _auto_backup_thread = threading.Thread(
        target=_auto_backup_loop,
        args=(app,),
        daemon=True,
        name='webdav-auto-backup'
    )
    _auto_backup_thread.start()
    app.logger.info("WebDAV 自动备份线程已启动")


def _auto_backup_loop(app):
    """自动备份循环（每5分钟检查一次）"""
    import time
    
    while not _auto_backup_stop.is_set():
        try:
            with app.app_context():
                settings = SiteSettings.get_settings()
                
                if (settings.webdav_auto_backup and 
                    settings.webdav_url and 
                    settings.webdav_username and 
                    settings.webdav_password):
                    
                    # 计算是否需要备份
                    need_backup = False
                    
                    if not settings.webdav_last_backup_time:
                        need_backup = True  # 从未备份过
                    else:
                        from datetime import timedelta
                        interval = timedelta(hours=settings.webdav_backup_interval or 24)
                        if datetime.utcnow() - settings.webdav_last_backup_time >= interval:
                            need_backup = True
                    
                    if need_backup:
                        _do_auto_backup(app, settings)
                        
        except Exception as e:
            try:
                app.logger.error(f"自动备份检查异常: {str(e)}")
            except Exception:
                pass
        
        # 等待5分钟再检查（可被 stop 事件中断）
        _auto_backup_stop.wait(300)


def _do_auto_backup(app, settings):
    """执行自动备份"""
    if not _backup_lock.acquire(blocking=False):
        app.logger.info("自动备份跳过：有其他备份任务正在执行")
        return
    
    try:
        from app.utils.webdav_client import WebDAVClient, decrypt_password
        
        # 创建本地备份
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        filename = f"booknav_auto_{timestamp}.db3"
        backup_dir = os.path.join(app.root_path, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, filename)
        
        db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if not os.path.isabs(db_path):
            db_path = os.path.join(app.root_path, db_path)
        
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"数据库文件不存在: {db_path}")
        
        shutil.copy2(db_path, backup_path)
        
        # 压缩为 .zip
        zip_filename = f"booknav_auto_{timestamp}.zip"
        zip_path = os.path.join(backup_dir, zip_filename)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(backup_path, filename)  # 压缩包内保留原始 .db3 文件名
        
        # 上传到 WebDAV
        password = decrypt_password(settings.webdav_password, app.config['SECRET_KEY'])
        client = WebDAVClient(
            url=settings.webdav_url,
            username=settings.webdav_username,
            password=password,
            timeout=60
        )
        
        remote_path = (settings.webdav_path or '/nav_backups/').rstrip('/') + '/' + zip_filename
        try:
            success, msg = client.upload_file(remote_path, zip_path)
        finally:
            # 清理临时 zip 文件
            if os.path.exists(zip_path):
                os.remove(zip_path)
        
        # 更新状态
        settings.webdav_last_backup_time = datetime.utcnow()
        if success:
            settings.webdav_last_backup_status = f"success|自动备份成功: {zip_filename}"
            app.logger.info(f"自动备份成功: {zip_filename}")
            
            # 清理超出保留数量的远端备份
            _cleanup_remote_backups(client, settings)
        else:
            settings.webdav_last_backup_status = f"failed|自动备份失败: {msg}"
            app.logger.error(f"自动备份上传失败: {msg}")
        
        db.session.commit()
        
    except Exception as e:
        try:
            settings.webdav_last_backup_time = datetime.utcnow()
            settings.webdav_last_backup_status = f"failed|自动备份异常: {str(e)}"
            db.session.commit()
            app.logger.error(f"自动备份异常: {str(e)}")
        except Exception:
            pass
    finally:
        _backup_lock.release()


def _cleanup_remote_backups(client, settings):
    """清理超出保留数量的远端备份"""
    try:
        keep_count = settings.webdav_backup_keep_count or 10
        remote_dir = settings.webdav_path or '/nav_backups/'
        
        success, result = client.list_files(remote_dir)
        if not success or not isinstance(result, list):
            return
        
        # 处理 .zip 和 .db3 备份文件（兼容新旧格式）
        backup_files = [f for f in result if f['name'].endswith('.zip') or f['name'].endswith('.db3')]
        
        # 按名称排序（文件名包含时间戳，降序 = 最新在前）
        backup_files.sort(key=lambda x: x['name'], reverse=True)
        
        # 删除超出保留数量的备份
        if len(backup_files) > keep_count:
            for old_file in backup_files[keep_count:]:
                remote_path = remote_dir.rstrip('/') + '/' + old_file['name']
                client.delete_file(remote_path)
    except Exception:
        pass  # 清理失败不影响主流程


# ==================== 本地备份路由 ====================

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
    
    # 获取站点设置（WebDAV 配置）
    settings = SiteSettings.get_settings()
    
    return render_template('admin/backup_list.html', title='备份管理', backups=backups, settings=settings)


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


# ==================== WebDAV 云端备份路由 ====================

@bp.route('/webdav-save-config', methods=['POST'])
@login_required
@superadmin_required
def webdav_save_config():
    """保存 WebDAV 配置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '无效的请求数据'}), 400
        
        settings = SiteSettings.get_settings()
        
        from app.utils.webdav_client import encrypt_password, decrypt_password
        
        # 更新基本配置
        webdav_url = (data.get('webdav_url') or '').strip()
        if webdav_url:
            # 智能修正 URL
            if not webdav_url.startswith(('http://', 'https://')):
                webdav_url = 'https://' + webdav_url
        
        settings.webdav_url = webdav_url
        settings.webdav_username = (data.get('webdav_username') or '').strip()
        
        # 密码处理：不为空且不是掩码时才更新
        new_password = data.get('webdav_password', '')
        if new_password and '****' not in new_password:
            settings.webdav_password = encrypt_password(
                new_password, current_app.config['SECRET_KEY']
            )
        
        # 备份路径
        webdav_path = (data.get('webdav_path') or '/nav_backups/').strip()
        if not webdav_path.startswith('/'):
            webdav_path = '/' + webdav_path
        if not webdav_path.endswith('/'):
            webdav_path = webdav_path + '/'
        settings.webdav_path = webdav_path
        
        # 自动备份设置
        settings.webdav_auto_backup = bool(data.get('webdav_auto_backup'))
        
        interval = data.get('webdav_backup_interval')
        if interval:
            settings.webdav_backup_interval = max(1, min(720, int(interval)))
        
        keep_count = data.get('webdav_backup_keep_count')
        if keep_count:
            settings.webdav_backup_keep_count = max(1, min(100, int(keep_count)))
        
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'WebDAV 配置已保存'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'保存失败: {str(e)}'}), 500


@bp.route('/webdav-test', methods=['POST'])
@login_required
@superadmin_required
def webdav_test():
    """测试 WebDAV 连接"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '无效的请求数据'}), 400
        
        webdav_url = (data.get('webdav_url') or '').strip()
        username = (data.get('webdav_username') or '').strip()
        password = (data.get('webdav_password') or '').strip()
        webdav_path = (data.get('webdav_path') or '/nav_backups/').strip()
        
        if not webdav_url or not username:
            return jsonify({'success': False, 'message': '请填写 WebDAV 地址和用户名'})
        
        # 如果密码是掩码，从数据库获取真实密码
        if not password or '****' in password:
            settings = SiteSettings.get_settings()
            if settings.webdav_password:
                from app.utils.webdav_client import decrypt_password
                password = decrypt_password(settings.webdav_password, current_app.config['SECRET_KEY'])
            else:
                return jsonify({'success': False, 'message': '请填写密码'})
        
        # 智能修正 URL
        if not webdav_url.startswith(('http://', 'https://')):
            webdav_url = 'https://' + webdav_url
        
        from app.utils.webdav_client import WebDAVClient
        
        client = WebDAVClient(
            url=webdav_url,
            username=username,
            password=password,
            timeout=15
        )
        
        # 测试连接
        success, message = client.test_connection()
        
        if success:
            # 额外测试备份目录
            if not webdav_path.startswith('/'):
                webdav_path = '/' + webdav_path
            
            dir_success, dir_msg = client.ensure_directory(webdav_path.strip('/'))
            if dir_success:
                message += f"，备份目录已就绪"
            else:
                message += f"（警告：备份目录创建失败 - {dir_msg}）"
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'测试错误: {str(e)}'}), 500


@bp.route('/webdav-upload/<filename>', methods=['POST'])
@login_required
@superadmin_required
def webdav_upload(filename):
    """上传本地备份到 WebDAV（压缩为 .zip 后上传）"""
    # 安全检查
    if os.path.sep in filename or '..' in filename:
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    if not _backup_lock.acquire(blocking=False):
        return jsonify({'success': False, 'message': '有其他备份任务正在执行，请稍后再试'})
    
    try:
        backup_dir = os.path.join(current_app.root_path, 'backups')
        local_path = os.path.join(backup_dir, filename)
        
        if not os.path.exists(local_path):
            return jsonify({'success': False, 'message': '本地备份文件不存在'})
        
        settings = SiteSettings.get_settings()
        if not settings.webdav_url or not settings.webdav_username or not settings.webdav_password:
            return jsonify({'success': False, 'message': '请先配置 WebDAV 连接信息'})
        
        from app.utils.webdav_client import WebDAVClient, decrypt_password
        
        password = decrypt_password(settings.webdav_password, current_app.config['SECRET_KEY'])
        client = WebDAVClient(
            url=settings.webdav_url,
            username=settings.webdav_username,
            password=password,
            timeout=60
        )
        
        # 压缩为 .zip 后上传（兼容蓝奏云等有文件类型限制的存储）
        zip_filename = os.path.splitext(filename)[0] + '.zip'
        zip_path = os.path.join(backup_dir, zip_filename)
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(local_path, filename)  # 压缩包内保留原始文件名
            
            remote_path = (settings.webdav_path or '/nav_backups/').rstrip('/') + '/' + zip_filename
            success, message = client.upload_file(remote_path, zip_path)
        finally:
            # 清理临时 zip 文件
            if os.path.exists(zip_path):
                os.remove(zip_path)
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'上传错误: {str(e)}'}), 500
    finally:
        _backup_lock.release()


@bp.route('/webdav-backup-now', methods=['POST'])
@login_required
@superadmin_required
def webdav_backup_now():
    """立即创建备份并上传到 WebDAV"""
    if not _backup_lock.acquire(blocking=False):
        return jsonify({'success': False, 'message': '有其他备份任务正在执行，请稍后再试'})
    
    try:
        settings = SiteSettings.get_settings()
        if not settings.webdav_url or not settings.webdav_username or not settings.webdav_password:
            return jsonify({'success': False, 'message': '请先配置 WebDAV 连接信息'})
        
        # 1. 创建本地备份
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        filename = f"booknav_{timestamp}.db3"
        backup_dir = os.path.join(current_app.root_path, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, filename)
        
        db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if not os.path.isabs(db_path):
            db_path = os.path.join(current_app.root_path, db_path)
        
        if not os.path.exists(db_path):
            return jsonify({'success': False, 'message': '数据库文件不存在'})
        
        shutil.copy2(db_path, backup_path)
        
        # 2. 压缩为 .zip
        zip_filename = f"booknav_{timestamp}.zip"
        zip_path = os.path.join(backup_dir, zip_filename)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(backup_path, filename)  # 压缩包内保留原始 .db3 文件名
        
        # 3. 上传到 WebDAV
        from app.utils.webdav_client import WebDAVClient, decrypt_password
        
        password = decrypt_password(settings.webdav_password, current_app.config['SECRET_KEY'])
        client = WebDAVClient(
            url=settings.webdav_url,
            username=settings.webdav_username,
            password=password,
            timeout=60
        )
        
        remote_path = (settings.webdav_path or '/nav_backups/').rstrip('/') + '/' + zip_filename
        try:
            success, message = client.upload_file(remote_path, zip_path)
        finally:
            # 清理临时 zip 文件
            if os.path.exists(zip_path):
                os.remove(zip_path)
        
        if success:
            # 更新最后备份状态
            settings.webdav_last_backup_time = datetime.utcnow()
            settings.webdav_last_backup_status = f"success|手动备份成功: {zip_filename}"
            db.session.commit()
            
            # 清理超出保留数量的远端备份
            _cleanup_remote_backups(client, settings)
        
        return jsonify({
            'success': success,
            'message': f'备份并上传成功: {zip_filename}' if success else f'上传失败: {message}',
            'filename': zip_filename
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'备份错误: {str(e)}'}), 500
    finally:
        _backup_lock.release()


@bp.route('/webdav-list', methods=['GET'])
@login_required
@superadmin_required
def webdav_list():
    """获取 WebDAV 远端备份列表"""
    try:
        settings = SiteSettings.get_settings()
        if not settings.webdav_url or not settings.webdav_username or not settings.webdav_password:
            return jsonify({'success': False, 'message': '请先配置 WebDAV 连接信息', 'files': []})
        
        from app.utils.webdav_client import WebDAVClient, decrypt_password
        
        password = decrypt_password(settings.webdav_password, current_app.config['SECRET_KEY'])
        client = WebDAVClient(
            url=settings.webdav_url,
            username=settings.webdav_username,
            password=password,
            timeout=15
        )
        
        remote_dir = settings.webdav_path or '/nav_backups/'
        success, result = client.list_files(remote_dir)
        
        if success:
            # 返回 .zip 和 .db3 备份文件（兼容新旧格式）
            files = [f for f in result if f['name'].endswith('.zip') or f['name'].endswith('.db3')]
            return jsonify({'success': True, 'files': files})
        else:
            return jsonify({'success': False, 'message': result, 'files': []})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取列表错误: {str(e)}', 'files': []}), 500


@bp.route('/webdav-download/<filename>', methods=['POST'])
@login_required
@superadmin_required
def webdav_download(filename):
    """从 WebDAV 下载备份到本地（自动解压 .zip）"""
    # 安全检查
    if os.path.sep in filename or '..' in filename:
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    try:
        settings = SiteSettings.get_settings()
        if not settings.webdav_url or not settings.webdav_username or not settings.webdav_password:
            return jsonify({'success': False, 'message': '请先配置 WebDAV 连接信息'})
        
        from app.utils.webdav_client import WebDAVClient, decrypt_password
        
        password = decrypt_password(settings.webdav_password, current_app.config['SECRET_KEY'])
        client = WebDAVClient(
            url=settings.webdav_url,
            username=settings.webdav_username,
            password=password,
            timeout=60
        )
        
        # 下载到本地备份目录
        backup_dir = os.path.join(current_app.root_path, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        local_path = os.path.join(backup_dir, filename)
        
        remote_path = (settings.webdav_path or '/nav_backups/').rstrip('/') + '/' + filename
        success, message = client.download_file(remote_path, local_path)
        
        if success and filename.endswith('.zip'):
            # 下载的是 zip 文件，解压出 .db3 文件
            try:
                with zipfile.ZipFile(local_path, 'r') as zf:
                    # 找到压缩包内的 .db3 文件
                    db_files = [n for n in zf.namelist() if n.endswith('.db3')]
                    if db_files:
                        # 解压 .db3 文件到备份目录
                        for db_file in db_files:
                            zf.extract(db_file, backup_dir)
                        extracted_name = db_files[0]
                        message = f"下载并解压成功: {extracted_name}"
                    else:
                        # zip 内无 .db3 文件，保留 zip 本身
                        message = "下载成功（压缩包内未找到 .db3 文件，已保留原始 zip）"
                
                # 解压成功后删除 zip 文件
                if db_files:
                    os.remove(local_path)
            except zipfile.BadZipFile:
                message = "下载成功，但文件不是有效的 zip 格式，已保留原始文件"
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'下载错误: {str(e)}'}), 500


@bp.route('/webdav-delete/<filename>', methods=['POST'])
@login_required
@superadmin_required
def webdav_delete(filename):
    """删除 WebDAV 远端备份"""
    # 安全检查
    if os.path.sep in filename or '..' in filename:
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    try:
        settings = SiteSettings.get_settings()
        if not settings.webdav_url or not settings.webdav_username or not settings.webdav_password:
            return jsonify({'success': False, 'message': '请先配置 WebDAV 连接信息'})
        
        from app.utils.webdav_client import WebDAVClient, decrypt_password
        
        password = decrypt_password(settings.webdav_password, current_app.config['SECRET_KEY'])
        client = WebDAVClient(
            url=settings.webdav_url,
            username=settings.webdav_username,
            password=password,
            timeout=15
        )
        
        remote_path = (settings.webdav_path or '/nav_backups/').rstrip('/') + '/' + filename
        success, message = client.delete_file(remote_path)
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'删除错误: {str(e)}'}), 500


@bp.route('/webdav-status', methods=['GET'])
@login_required
@superadmin_required
def webdav_status():
    """获取 WebDAV 备份状态"""
    try:
        settings = SiteSettings.get_settings()
        
        status_text = settings.webdav_last_backup_status or ''
        status_type = 'none'
        status_msg = '从未备份'
        
        if status_text:
            parts = status_text.split('|', 1)
            if len(parts) == 2:
                status_type = parts[0]
                status_msg = parts[1]
            else:
                status_msg = status_text
        
        last_time = ''
        if settings.webdav_last_backup_time:
            last_time = settings.webdav_last_backup_time.strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify({
            'success': True,
            'status_type': status_type,
            'status_message': status_msg,
            'last_backup_time': last_time,
            'auto_backup_enabled': settings.webdav_auto_backup,
            'backup_interval': settings.webdav_backup_interval or 24
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== 工具函数 ====================

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
