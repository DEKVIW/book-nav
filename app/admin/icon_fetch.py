#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图标批量抓取路由"""

import time
import threading
from queue import Queue
from urllib.parse import urlparse
from flask import jsonify, current_app
from flask_login import login_required
from app import db
from app.admin import bp
from app.admin.decorators import superadmin_required
from app.models import Website
from app.main.utils import get_website_icon
import requests


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

