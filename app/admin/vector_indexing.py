#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""向量批量生成路由"""

import time
import threading
from flask import jsonify, current_app, request
from flask_login import login_required
from app import db
from app.admin import bp
from app.admin.decorators import superadmin_required
from app.models import Website, Category, SiteSettings
from app.utils.vector_service import EmbeddingClient, QdrantVectorStore, VectorSearchService
from qdrant_client import QdrantClient


# 用于存储批量处理的状态
vector_indexing_status = {
    'is_running': False,
    'total': 0,
    'processed': 0,
    'success': 0,
    'failed': 0,
    'skipped': 0,
    'start_time': None,
    'should_stop': False
}


def check_existing_vectors(qdrant_url: str, website_ids: list) -> set:
    """
    检查 Qdrant 中已存在的向量（用于跳过已生成的网站）
    
    Args:
        qdrant_url: Qdrant 服务地址
        website_ids: 网站ID列表
        
    Returns:
        已存在向量的网站ID集合
    """
    try:
        client = QdrantClient(url=qdrant_url)
        collections = client.get_collections().collections
        collection_names = [c.name for c in collections]
        
        if 'websites' not in collection_names:
            return set()
        
        # 批量查询已存在的向量
        existing_ids = set()
        batch_size = 100
        
        for i in range(0, len(website_ids), batch_size):
            batch_ids = website_ids[i:i+batch_size]
            try:
                # 使用 retrieve 方法批量获取这些ID的向量
                result = client.retrieve(
                    collection_name='websites',
                    ids=batch_ids
                )
                # 从返回结果中提取ID（成功返回的说明已存在）
                for point in result:
                    existing_ids.add(point.id)
            except Exception:
                # 如果查询失败，继续处理
                pass
        
        return existing_ids
    except Exception as e:
        current_app.logger.warning(f"检查已存在向量时出错: {str(e)}")
        return set()


def process_vector_indexing(app, skip_existing: bool = True):
    """后台处理所有网站的向量生成"""
    global vector_indexing_status
    
    with app.app_context():
        try:
            # 获取配置
            settings = SiteSettings.get_settings()
            
            if not settings:
                vector_indexing_status['is_running'] = False
                current_app.logger.error("无法获取站点设置")
                return
            
            if not all([settings.ai_api_base_url, settings.ai_api_key, settings.embedding_model]):
                vector_indexing_status['is_running'] = False
                current_app.logger.error("AI搜索配置不完整")
                return
            
            if not settings.qdrant_url:
                vector_indexing_status['is_running'] = False
                current_app.logger.error("Qdrant URL 未配置")
                return
            
            # 初始化向量服务
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
            
            # 获取所有网站
            websites = Website.query.all()
            total_count = len(websites)
            vector_indexing_status['total'] = total_count
            
            current_app.logger.info(f"开始批量生成向量，共 {total_count} 个网站")
            
            # 检查已存在的向量
            existing_ids = set()
            if skip_existing:
                current_app.logger.info("检查已存在的向量...")
                website_ids = [w.id for w in websites]
                existing_ids = check_existing_vectors(settings.qdrant_url, website_ids)
                if existing_ids:
                    current_app.logger.info(f"发现 {len(existing_ids)} 个网站已有向量，将跳过")
            
            # 处理每个网站
            for website in websites:
                # 检查是否应该停止
                if vector_indexing_status['should_stop']:
                    current_app.logger.info("收到停止信号，中断批量生成")
                    break
                
                # 检查是否已存在
                if skip_existing and website.id in existing_ids:
                    vector_indexing_status['skipped'] += 1
                    vector_indexing_status['processed'] += 1
                    continue
                
                try:
                    # 获取分类名称
                    category_name = ""
                    if website.category_id:
                        category = Category.query.get(website.category_id)
                        if category:
                            category_name = category.name
                    
                    # 生成向量
                    success = vector_service.index_website(
                        website_id=website.id,
                        title=website.title or "",
                        description=website.description or "",
                        category_name=category_name,
                        url=website.url or ""
                    )
                    
                    if success:
                        vector_indexing_status['success'] += 1
                    else:
                        vector_indexing_status['failed'] += 1
                    
                    vector_indexing_status['processed'] += 1
                    
                    # 每10个网站记录一次进度
                    if vector_indexing_status['processed'] % 10 == 0:
                        current_app.logger.info(
                            f"进度: {vector_indexing_status['processed']}/{total_count} "
                            f"(成功: {vector_indexing_status['success']}, "
                            f"失败: {vector_indexing_status['failed']}, "
                            f"跳过: {vector_indexing_status['skipped']})"
                        )
                    
                    # 适当延迟，避免API限流
                    time.sleep(0.1)
                    
                except Exception as e:
                    vector_indexing_status['failed'] += 1
                    vector_indexing_status['processed'] += 1
                    current_app.logger.error(f"网站 {website.id} 向量生成失败: {str(e)}")
            
            current_app.logger.info(
                f"批量生成向量完成！"
                f"成功: {vector_indexing_status['success']}, "
                f"失败: {vector_indexing_status['failed']}, "
                f"跳过: {vector_indexing_status['skipped']}"
            )
            
        except Exception as e:
            current_app.logger.error(f"批量生成向量任务出错: {str(e)}")
        finally:
            vector_indexing_status['is_running'] = False


@bp.route('/api/batch-generate-vectors', methods=['POST'])
@login_required
@superadmin_required
def batch_generate_vectors():
    """开始批量生成向量"""
    global vector_indexing_status
    
    # 检查是否已有正在进行的任务
    if vector_indexing_status['is_running']:
        return jsonify({
            'success': False,
            'message': '已有批量生成任务正在运行，请等待其完成'
        })
    
    # 检查配置
    settings = SiteSettings.get_settings()
    if not settings:
        return jsonify({
            'success': False,
            'message': '无法获取站点设置'
        })
    
    if not all([settings.ai_api_base_url, settings.ai_api_key, settings.embedding_model]):
        return jsonify({
            'success': False,
            'message': 'AI搜索配置不完整，请先配置API地址、密钥和模型'
        })
    
    if not settings.qdrant_url:
        return jsonify({
            'success': False,
            'message': 'Qdrant URL 未配置'
        })
    
    # 获取参数
    data = request.get_json() or {}
    skip_existing = data.get('skip_existing', True)
    
    # 重置状态
    vector_indexing_status = {
        'is_running': True,
        'total': 0,
        'processed': 0,
        'success': 0,
        'failed': 0,
        'skipped': 0,
        'start_time': time.time(),
        'should_stop': False
    }
    
    # 获取当前应用实例传递给线程
    app = current_app._get_current_object()
    
    # 启动后台线程处理
    threading.Thread(target=process_vector_indexing, args=(app, skip_existing), daemon=True).start()
    
    return jsonify({
        'success': True,
        'message': '批量生成向量任务已启动'
    })


@bp.route('/api/batch-generate-vectors/status')
@login_required
@superadmin_required
def batch_generate_vectors_status():
    """获取批量生成向量的状态"""
    global vector_indexing_status
    
    # 计算执行时间
    elapsed_time = ""
    if vector_indexing_status['start_time']:
        elapsed_seconds = int(time.time() - vector_indexing_status['start_time'])
        minutes, seconds = divmod(elapsed_seconds, 60)
        if minutes > 0:
            elapsed_time = f"{minutes}分{seconds}秒"
        else:
            elapsed_time = f"{seconds}秒"
    
    # 计算进度百分比
    percent = 0
    if vector_indexing_status['total'] > 0:
        percent = int((vector_indexing_status['processed'] / vector_indexing_status['total']) * 100)
    
    response = jsonify({
        'is_running': vector_indexing_status['is_running'],
        'total': vector_indexing_status['total'],
        'processed': vector_indexing_status['processed'],
        'success': vector_indexing_status['success'],
        'failed': vector_indexing_status['failed'],
        'skipped': vector_indexing_status['skipped'],
        'elapsed_time': elapsed_time,
        'percent': percent
    })
    
    # 添加禁用缓冲的头部
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    
    return response


@bp.route('/api/batch-generate-vectors/stop', methods=['POST'])
@login_required
@superadmin_required
def batch_generate_vectors_stop():
    """停止批量生成向量任务"""
    global vector_indexing_status
    
    if not vector_indexing_status['is_running']:
        return jsonify({
            'success': False,
            'message': '没有正在运行的生成任务'
        })
    
    # 设置停止标志
    vector_indexing_status['should_stop'] = True
    
    return jsonify({
        'success': True,
        'message': '已发送停止信号，任务将在当前网站处理完成后停止'
    })

