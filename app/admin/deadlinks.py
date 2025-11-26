#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""死链检测路由"""

import time
import json
import uuid
import queue
import csv
import io
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import render_template, redirect, url_for, flash, request, jsonify, Response, current_app
from flask_login import login_required, current_user
import requests
from app import db
from app.admin import bp
from app.admin.decorators import superadmin_required
from app.models import Website, DeadlinkCheck, OperationLog


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

