#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据管理路由（导入导出）"""

import os
import io
import shutil
import sqlite3
import tempfile
from datetime import datetime
from flask import render_template, redirect, url_for, flash, request, send_file, current_app
from flask_login import login_required, current_user
from app import db
from app.admin import bp
from app.admin.forms import DataImportForm
from app.admin.decorators import superadmin_required
from app.models import Category, Website


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


@bp.route('/clear-websites', methods=['POST'])
@login_required
@superadmin_required
def clear_websites():
    """清空所有网站链接数据"""
    from flask import jsonify
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


@bp.route('/clear-all-data', methods=['POST'])
@login_required
@superadmin_required
def clear_all_data():
    """清空所有网站和分类数据"""
    from flask import jsonify
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

