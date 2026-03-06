#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""备份管理路由（本地备份 + 多云端 WebDAV 备份）"""

import os
import shutil
import threading
import zipfile
from datetime import datetime, timedelta
from flask import (
    render_template, redirect, url_for, flash, send_file,
    abort, current_app, request, jsonify
)
from flask_login import login_required
from app import db
from app.admin import bp
from app.admin.decorators import superadmin_required
from app.models import WebDAVConfig


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
    """自动备份循环（每5分钟检查一次所有启用的云端配置）"""
    while not _auto_backup_stop.is_set():
        try:
            with app.app_context():
                # 查询所有启用了自动备份的配置
                configs = WebDAVConfig.query.filter_by(
                    auto_backup=True, enabled=True
                ).all()
                
                for config in configs:
                    try:
                        if _should_backup(config):
                            _do_auto_backup(app, config)
                    except Exception as e:
                        app.logger.error(f"自动备份 [{config.name}] 异常: {str(e)}")
                        
        except Exception as e:
            try:
                db.session.rollback()  # 确保 session 干净
                app.logger.error(f"自动备份检查异常: {str(e)}")
            except Exception:
                pass
        
        # 等待5分钟再检查（可被 stop 事件中断）
        _auto_backup_stop.wait(300)


def _should_backup(config):
    """判断某个配置是否需要执行自动备份"""
    if not config.webdav_url or not config.webdav_username or not config.webdav_password:
        return False
    
    if not config.last_backup_time:
        return True  # 从未备份过
    
    time_since_last = datetime.utcnow() - config.last_backup_time
    interval = timedelta(hours=config.backup_interval or 24)
    
    # 正常间隔到了 → 备份
    if time_since_last >= interval:
        return True
    
    # 如果上次是失败的，30 分钟后就重试（而非等完整间隔）
    if config.last_backup_status and config.last_backup_status.startswith('failed|'):
        return time_since_last >= timedelta(minutes=30)
    
    return False


def _do_auto_backup(app, config):
    """对指定云端配置执行自动备份"""
    if not _backup_lock.acquire(blocking=False):
        app.logger.info(f"自动备份 [{config.name}] 跳过：有其他备份任务正在执行")
        return
    
    try:
        from app.utils.webdav_client import WebDAVClient, decrypt_password
        
        # 创建本地备份（文件名含 config_id 避免多配置冲突）
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        db_filename = f"booknav_auto_c{config.id}_{timestamp}.db3"
        backup_dir = os.path.join(app.root_path, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, db_filename)
        
        db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if not os.path.isabs(db_path):
            db_path = os.path.join(app.root_path, db_path)
        
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"数据库文件不存在: {db_path}")
        
        shutil.copy2(db_path, backup_path)
        
        # 压缩为 .zip
        zip_filename = f"booknav_auto_c{config.id}_{timestamp}.zip"
        zip_path = os.path.join(backup_dir, zip_filename)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(backup_path, db_filename)
        
        # 压缩完成后立即删除临时 .db3，避免自动备份不断积累本地文件
        if os.path.exists(backup_path):
            os.remove(backup_path)
        
        # 上传到 WebDAV
        password = decrypt_password(config.webdav_password, app.config['SECRET_KEY'])
        client = WebDAVClient(
            url=config.webdav_url,
            username=config.webdav_username,
            password=password,
            timeout=60
        )
        
        remote_path = (config.webdav_path or '/nav_backups/').rstrip('/') + '/' + zip_filename
        try:
            success, msg = client.upload_file(remote_path, zip_path)
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        
        # 更新状态
        config.last_backup_time = datetime.utcnow()
        if success:
            config.last_backup_status = f"success|自动备份成功: {zip_filename}"
            app.logger.info(f"自动备份 [{config.name}] 成功: {zip_filename}")
            _cleanup_remote_backups(client, config)
        else:
            config.last_backup_status = f"failed|自动备份失败: {msg}"
            app.logger.error(f"自动备份 [{config.name}] 上传失败: {msg}")
        
        db.session.commit()
        
    except Exception as e:
        try:
            db.session.rollback()  # 先回滚脏状态，再写入失败信息
            config.last_backup_time = datetime.utcnow()
            config.last_backup_status = f"failed|自动备份异常: {str(e)}"
            db.session.commit()
            app.logger.error(f"自动备份 [{config.name}] 异常: {str(e)}")
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
    finally:
        _backup_lock.release()


def _cleanup_remote_backups(client, config):
    """清理超出保留数量的远端备份"""
    try:
        keep_count = config.backup_keep_count or 10
        remote_dir = config.webdav_path or '/nav_backups/'
        
        success, result = client.list_files(remote_dir)
        if not success or not isinstance(result, list):
            return
        
        backup_files = [f for f in result if f['name'].endswith('.zip') or f['name'].endswith('.db3')]
        backup_files.sort(key=lambda x: x['name'], reverse=True)
        
        if len(backup_files) > keep_count:
            for old_file in backup_files[keep_count:]:
                remote_path = remote_dir.rstrip('/') + '/' + old_file['name']
                client.delete_file(remote_path)
    except Exception:
        pass


def _get_webdav_client(config, timeout=30):
    """从配置创建 WebDAV 客户端"""
    from app.utils.webdav_client import WebDAVClient, decrypt_password
    password = decrypt_password(config.webdav_password, current_app.config['SECRET_KEY'])
    return WebDAVClient(
        url=config.webdav_url,
        username=config.webdav_username,
        password=password,
        timeout=timeout
    )


# ==================== 本地备份路由 ====================

@bp.route('/backup-data')
@login_required
@superadmin_required
def backup_data():
    """创建数据库备份"""
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    filename = f"booknav_{timestamp}.db3"
    backup_dir = os.path.join(current_app.root_path, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, filename)
    
    try:
        db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if not os.path.isabs(db_path):
            db_path = os.path.join(current_app.root_path, db_path)
        
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"找不到数据库文件: {db_path}")
        
        shutil.copy2(db_path, backup_path)
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
    backup_dir = os.path.join(current_app.root_path, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    
    backups = []
    for filename in os.listdir(backup_dir):
        if filename.endswith('.db3'):
            file_path = os.path.join(backup_dir, filename)
            file_stats = os.stat(file_path)
            
            try:
                time_str = filename.split('_')[1].split('.')[0]
                backup_time = datetime.strptime(time_str, '%Y%m%d%H%M%S')
                time_display = backup_time.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                time_display = datetime.fromtimestamp(file_stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            
            backups.append({
                'filename': filename,
                'size': file_stats.st_size,
                'size_display': format_file_size(file_stats.st_size),
                'time': file_stats.st_mtime,
                'time_display': time_display
            })
    
    backups.sort(key=lambda x: x['time'], reverse=True)
    
    # 获取所有 WebDAV 配置
    webdav_configs = WebDAVConfig.query.order_by(WebDAVConfig.created_at).all()
    
    return render_template('admin/backup_list.html',
                           title='备份管理',
                           backups=backups,
                           webdav_configs=webdav_configs)


@bp.route('/download-backup/<filename>')
@login_required
@superadmin_required
def download_backup(filename):
    """下载备份文件"""
    if os.path.sep in filename or '..' in filename:
        abort(404)
    
    backup_dir = os.path.join(current_app.root_path, 'backups')
    backup_path = os.path.join(backup_dir, filename)
    
    if not os.path.exists(backup_path):
        flash('备份文件不存在', 'danger')
        return redirect(url_for('admin.backup_list'))
    
    try:
        return send_file(backup_path, as_attachment=True, download_name=filename)
    except Exception as e:
        flash(f'下载失败: {str(e)}', 'danger')
        return redirect(url_for('admin.backup_list'))


@bp.route('/delete-backup/<filename>', methods=['POST'])
@login_required
@superadmin_required
def delete_backup(filename):
    """删除备份文件"""
    if os.path.sep in filename or '..' in filename:
        abort(404)
    
    backup_dir = os.path.join(current_app.root_path, 'backups')
    backup_path = os.path.join(backup_dir, filename)
    
    if not os.path.exists(backup_path):
        flash('备份文件不存在', 'danger')
        return redirect(url_for('admin.backup_list'))
    
    try:
        os.remove(backup_path)
        flash('备份文件已删除', 'success')
    except Exception as e:
        flash(f'删除失败: {str(e)}', 'danger')
    
    return redirect(url_for('admin.backup_list'))


@bp.route('/restore-backup/<filename>', methods=['POST'])
@login_required
@superadmin_required
def restore_backup(filename):
    """恢复备份"""
    if os.path.sep in filename or '..' in filename:
        abort(404)
    
    backup_dir = os.path.join(current_app.root_path, 'backups')
    backup_path = os.path.join(backup_dir, filename)
    
    if not os.path.exists(backup_path):
        flash('备份文件不存在', 'danger')
        return redirect(url_for('admin.backup_list'))
    
    try:
        db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if not os.path.isabs(db_path):
            db_path = os.path.join(current_app.root_path, db_path)
        
        db.session.close()
        db.engine.dispose()
        
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        temp_backup = f"{db_path}.restore_bak.{timestamp}"
        shutil.copy2(db_path, temp_backup)
        shutil.copy2(backup_path, db_path)
        
        flash('数据库恢复成功，请重新登录', 'success')
        return redirect(url_for('auth.logout'))
    except Exception as e:
        flash(f'恢复失败: {str(e)}', 'danger')
        return redirect(url_for('admin.backup_list'))


# ==================== WebDAV 配置 CRUD ====================

@bp.route('/webdav-config-save', methods=['POST'])
@login_required
@superadmin_required
def webdav_config_save():
    """创建或更新 WebDAV 配置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '无效的请求数据'}), 400
        
        from app.utils.webdav_client import encrypt_password
        
        config_id = data.get('id')
        
        if config_id:
            # 更新已有配置
            config = WebDAVConfig.query.get(config_id)
            if not config:
                return jsonify({'success': False, 'message': '配置不存在'}), 404
        else:
            # 创建新配置
            config = WebDAVConfig()
            db.session.add(config)
        
        # 名称
        config.name = (data.get('name') or '我的云端备份').strip()
        
        # URL 智能修正
        webdav_url = (data.get('webdav_url') or '').strip()
        if webdav_url and not webdav_url.startswith(('http://', 'https://')):
            webdav_url = 'https://' + webdav_url
        config.webdav_url = webdav_url
        
        # 用户名
        config.webdav_username = (data.get('webdav_username') or '').strip()
        
        # 密码：不为空且不是掩码时才更新
        new_password = data.get('webdav_password', '')
        if new_password and '****' not in new_password:
            config.webdav_password = encrypt_password(
                new_password, current_app.config['SECRET_KEY']
            )
        
        # 备份路径
        webdav_path = (data.get('webdav_path') or '/nav_backups/').strip()
        if not webdav_path.startswith('/'):
            webdav_path = '/' + webdav_path
        if not webdav_path.endswith('/'):
            webdav_path = webdav_path + '/'
        config.webdav_path = webdav_path
        
        # 启用状态
        config.enabled = bool(data.get('enabled', True))
        
        # 自动备份设置
        config.auto_backup = bool(data.get('auto_backup'))
        
        interval = data.get('backup_interval')
        if interval:
            config.backup_interval = max(1, min(720, int(interval)))
        
        keep_count = data.get('backup_keep_count')
        if keep_count:
            config.backup_keep_count = max(1, min(100, int(keep_count)))
        
        db.session.commit()
        
        action = '更新' if config_id else '创建'
        return jsonify({
            'success': True,
            'message': f'云端配置已{action}: {config.name}',
            'config': config.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'保存失败: {str(e)}'}), 500


@bp.route('/webdav-config-delete/<int:config_id>', methods=['POST'])
@login_required
@superadmin_required
def webdav_config_delete(config_id):
    """删除 WebDAV 配置"""
    try:
        config = WebDAVConfig.query.get(config_id)
        if not config:
            return jsonify({'success': False, 'message': '配置不存在'}), 404
        
        name = config.name
        db.session.delete(config)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'已删除云端配置: {name}'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


@bp.route('/webdav-config-get/<int:config_id>', methods=['GET'])
@login_required
@superadmin_required
def webdav_config_get(config_id):
    """获取单个 WebDAV 配置详情（用于编辑弹窗回填）"""
    try:
        config = WebDAVConfig.query.get(config_id)
        if not config:
            return jsonify({'success': False, 'message': '配置不存在'}), 404
        
        return jsonify({'success': True, 'config': config.to_dict()})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== WebDAV 操作路由（按 config_id） ====================

@bp.route('/webdav-test/<int:config_id>', methods=['POST'])
@login_required
@superadmin_required
def webdav_test(config_id):
    """测试 WebDAV 连接"""
    try:
        config = WebDAVConfig.query.get(config_id)
        if not config:
            return jsonify({'success': False, 'message': '配置不存在'}), 404
        
        if not config.webdav_url or not config.webdav_username or not config.webdav_password:
            return jsonify({'success': False, 'message': '请先完善配置信息（地址、用户名、密码）'})
        
        client = _get_webdav_client(config, timeout=15)
        success, message = client.test_connection()
        
        if success:
            webdav_path = config.webdav_path or '/nav_backups/'
            if not webdav_path.startswith('/'):
                webdav_path = '/' + webdav_path
            
            dir_success, dir_msg = client.ensure_directory(webdav_path.strip('/'))
            if dir_success:
                message += "，备份目录已就绪"
            else:
                message += f"（警告：备份目录 - {dir_msg}）"
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'测试错误: {str(e)}'}), 500


@bp.route('/webdav-test-form', methods=['POST'])
@login_required
@superadmin_required
def webdav_test_form():
    """测试 WebDAV 连接（从表单数据，用于新建配置时测试）"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '无效的请求数据'}), 400
        
        webdav_url = (data.get('webdav_url') or '').strip()
        username = (data.get('webdav_username') or '').strip()
        password = (data.get('webdav_password') or '').strip()
        webdav_path = (data.get('webdav_path') or '/nav_backups/').strip()
        config_id = data.get('config_id')
        
        if not webdav_url or not username:
            return jsonify({'success': False, 'message': '请填写 WebDAV 地址和用户名'})
        
        # 密码为空或掩码时，从已有配置获取
        if (not password or '****' in password) and config_id:
            existing = WebDAVConfig.query.get(config_id)
            if existing and existing.webdav_password:
                from app.utils.webdav_client import decrypt_password
                password = decrypt_password(existing.webdav_password, current_app.config['SECRET_KEY'])
            else:
                return jsonify({'success': False, 'message': '请填写密码'})
        elif not password:
            return jsonify({'success': False, 'message': '请填写密码'})
        
        if not webdav_url.startswith(('http://', 'https://')):
            webdav_url = 'https://' + webdav_url
        
        from app.utils.webdav_client import WebDAVClient
        
        client = WebDAVClient(url=webdav_url, username=username, password=password, timeout=15)
        success, message = client.test_connection()
        
        if success:
            if not webdav_path.startswith('/'):
                webdav_path = '/' + webdav_path
            dir_success, dir_msg = client.ensure_directory(webdav_path.strip('/'))
            if dir_success:
                message += "，备份目录已就绪"
            else:
                message += f"（警告：备份目录 - {dir_msg}）"
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'测试错误: {str(e)}'}), 500


@bp.route('/webdav-backup-now/<int:config_id>', methods=['POST'])
@login_required
@superadmin_required
def webdav_backup_now(config_id):
    """立即创建备份并上传到指定云端"""
    if not _backup_lock.acquire(blocking=False):
        return jsonify({'success': False, 'message': '有其他备份任务正在执行，请稍后再试'})
    
    try:
        config = WebDAVConfig.query.get(config_id)
        if not config:
            return jsonify({'success': False, 'message': '配置不存在'}), 404
        
        if not config.webdav_url or not config.webdav_username or not config.webdav_password:
            return jsonify({'success': False, 'message': '请先完善配置信息'})
        
        # 1. 创建本地备份
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        db_filename = f"booknav_{timestamp}.db3"
        backup_dir = os.path.join(current_app.root_path, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, db_filename)
        
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
            zf.write(backup_path, db_filename)
        
        # 压缩完成后删除临时 .db3（本地备份有独立入口，云备份不留本地文件）
        if os.path.exists(backup_path):
            os.remove(backup_path)
        
        # 3. 上传到 WebDAV
        client = _get_webdav_client(config, timeout=60)
        
        remote_path = (config.webdav_path or '/nav_backups/').rstrip('/') + '/' + zip_filename
        try:
            success, message = client.upload_file(remote_path, zip_path)
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        
        if success:
            config.last_backup_time = datetime.utcnow()
            config.last_backup_status = f"success|手动备份成功: {zip_filename}"
            db.session.commit()
            _cleanup_remote_backups(client, config)
        
        return jsonify({
            'success': success,
            'message': f'备份并上传成功: {zip_filename}' if success else f'上传失败: {message}',
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'备份错误: {str(e)}'}), 500
    finally:
        _backup_lock.release()


@bp.route('/webdav-upload-to/<int:config_id>/<filename>', methods=['POST'])
@login_required
@superadmin_required
def webdav_upload_to(config_id, filename):
    """上传本地备份到指定云端（压缩为 .zip）"""
    if os.path.sep in filename or '..' in filename:
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    if not _backup_lock.acquire(blocking=False):
        return jsonify({'success': False, 'message': '有其他备份任务正在执行，请稍后再试'})
    
    try:
        config = WebDAVConfig.query.get(config_id)
        if not config:
            return jsonify({'success': False, 'message': '配置不存在'}), 404
        
        backup_dir = os.path.join(current_app.root_path, 'backups')
        local_path = os.path.join(backup_dir, filename)
        
        if not os.path.exists(local_path):
            return jsonify({'success': False, 'message': '本地备份文件不存在'})
        
        client = _get_webdav_client(config, timeout=60)
        
        # 压缩为 .zip 后上传
        zip_filename = os.path.splitext(filename)[0] + '.zip'
        zip_path = os.path.join(backup_dir, zip_filename)
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(local_path, filename)
            
            remote_path = (config.webdav_path or '/nav_backups/').rstrip('/') + '/' + zip_filename
            success, message = client.upload_file(remote_path, zip_path)
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'上传错误: {str(e)}'}), 500
    finally:
        _backup_lock.release()


@bp.route('/webdav-list/<int:config_id>', methods=['GET'])
@login_required
@superadmin_required
def webdav_list(config_id):
    """获取指定云端的远端备份列表"""
    try:
        config = WebDAVConfig.query.get(config_id)
        if not config:
            return jsonify({'success': False, 'message': '配置不存在', 'files': []}), 404
        
        if not config.webdav_url or not config.webdav_username or not config.webdav_password:
            return jsonify({'success': False, 'message': '请先完善配置信息', 'files': []})
        
        client = _get_webdav_client(config, timeout=15)
        
        remote_dir = config.webdav_path or '/nav_backups/'
        success, result = client.list_files(remote_dir)
        
        if success:
            files = [f for f in result if f['name'].endswith('.zip') or f['name'].endswith('.db3')]
            return jsonify({'success': True, 'files': files})
        else:
            return jsonify({'success': False, 'message': result, 'files': []})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取列表错误: {str(e)}', 'files': []}), 500


@bp.route('/webdav-download/<int:config_id>/<filename>', methods=['POST'])
@login_required
@superadmin_required
def webdav_download(config_id, filename):
    """从指定云端下载备份到本地（自动解压 .zip）"""
    if os.path.sep in filename or '..' in filename:
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    try:
        config = WebDAVConfig.query.get(config_id)
        if not config:
            return jsonify({'success': False, 'message': '配置不存在'}), 404
        
        client = _get_webdav_client(config, timeout=60)
        
        backup_dir = os.path.join(current_app.root_path, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        local_path = os.path.join(backup_dir, filename)
        
        remote_path = (config.webdav_path or '/nav_backups/').rstrip('/') + '/' + filename
        success, message = client.download_file(remote_path, local_path)
        
        if success and filename.endswith('.zip'):
            try:
                with zipfile.ZipFile(local_path, 'r') as zf:
                    db_files = [n for n in zf.namelist() if n.endswith('.db3')]
                    if db_files:
                        for db_file in db_files:
                            zf.extract(db_file, backup_dir)
                        message = f"下载并解压成功: {db_files[0]}"
                    else:
                        message = "下载成功（压缩包内未找到 .db3 文件，已保留原始 zip）"
                
                if db_files:
                    os.remove(local_path)
            except zipfile.BadZipFile:
                message = "下载成功，但文件不是有效的 zip 格式，已保留原始文件"
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'下载错误: {str(e)}'}), 500


@bp.route('/webdav-delete-remote/<int:config_id>/<path:filename>', methods=['POST'])
@login_required
@superadmin_required
def webdav_delete_remote(config_id, filename):
    """删除指定云端的远端备份"""
    if '..' in filename:
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    try:
        config = WebDAVConfig.query.get(config_id)
        if not config:
            return jsonify({'success': False, 'message': '配置不存在'}), 404
        
        client = _get_webdav_client(config, timeout=15)
        
        remote_path = (config.webdav_path or '/nav_backups/').rstrip('/') + '/' + filename
        success, message = client.delete_file(remote_path)
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'删除错误: {str(e)}'}), 500


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
