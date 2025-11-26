#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""管理员工具函数"""

import threading
from datetime import datetime
import os
from flask import current_app, flash, url_for
from werkzeug.utils import secure_filename
from app.models import Category, Website, SiteSettings


def trigger_vector_indexing(website_id: int, category_name: str = None):
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

