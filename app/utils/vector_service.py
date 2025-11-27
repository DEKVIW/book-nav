#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""向量搜索服务工具类"""

import json
import requests
import numpy as np
from typing import List, Dict, Optional, Tuple
from flask import current_app
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from qdrant_client.http import models


class EmbeddingClient:
    """Embedding API 客户端（用于将文本转换为向量）"""
    
    def __init__(self, api_base_url: str, api_key: str, model_name: str = 'text-embedding-3-small'):
        """
        初始化 Embedding 客户端
        
        Args:
            api_base_url: API 基础 URL
            api_key: API 密钥
            model_name: 模型名称
        """
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model_name
        # 常见模型的默认维度（会在首次调用时自动检测）
        self.dimension = self._get_default_dimension(model_name)
    
    def _get_default_dimension(self, model_name: str) -> int:
        """根据模型名称返回默认维度"""
        dimension_map = {
            'text-embedding-3-small': 1536,
            'text-embedding-3-large': 3072,
            'text-embedding-ada-002': 1536,
            'bge-large-zh-v1.5': 1024,
            'bge-small-zh-v1.5': 512,
            'bge-m3': 1024,
            'bge-large-en-v1.5': 1024,
            'jina-embeddings-v2-base-zh': 768,
            'jina-embeddings-v2-base-code': 768,
            'text-embedding-004': 768,
            'gemini-embedding-001': 768,
            'embedding-001': 1536,
        }
        return dimension_map.get(model_name.lower(), 1024)  # 默认 1024
    
    def generate_embedding(self, text: str, max_retries: int = 3, use_cache: bool = True) -> List[float]:
        """
        生成文本的向量表示（带重试机制和缓存）
        
        Args:
            text: 输入文本
            max_retries: 最大重试次数
            use_cache: 是否使用缓存
            
        Returns:
            向量列表
        """
        if not text:
            text = ""
        
        # 检查缓存
        if use_cache:
            try:
                from app.utils.cache import get_cached_vector, cache_vector
                cached_vector = get_cached_vector(text, self.model_name)
                if cached_vector:
                    current_app.logger.debug(f"使用缓存的向量: {text[:50]}...")
                    return cached_vector
            except Exception as e:
                current_app.logger.warning(f"缓存检查失败: {str(e)}")
        
        url = f"{self.api_base_url}/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.model_name,
            "input": text
        }
        
        last_error = None
        for attempt in range(max_retries):
            try:
                response = requests.post(url, json=data, headers=headers, timeout=30)
                
                # 处理 503 等服务器错误
                if response.status_code == 503:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 2  # 递增等待时间：2s, 4s, 6s
                        current_app.logger.warning(
                            f"Embedding API 返回 503，等待 {wait_time} 秒后重试 (尝试 {attempt + 1}/{max_retries})"
                        )
                        import time
                        time.sleep(wait_time)
                        continue
                    else:
                        raise Exception(f"Embedding API 服务不可用 (503)，已重试 {max_retries} 次")
                
                response.raise_for_status()
                result = response.json()
                embedding = result['data'][0]['embedding']
                # 自动检测并更新维度
                actual_dimension = len(embedding)
                if self.dimension != actual_dimension:
                    current_app.logger.info(f"检测到向量维度: {actual_dimension} (模型: {self.model_name})")
                    self.dimension = actual_dimension
                
                # 缓存向量
                if use_cache:
                    try:
                        from app.utils.cache import cache_vector
                        cache_vector(text, self.model_name, embedding)
                    except Exception as e:
                        current_app.logger.warning(f"向量缓存失败: {str(e)}")
                
                return embedding
                
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    current_app.logger.warning(f"Embedding API 调用超时，重试中 (尝试 {attempt + 1}/{max_retries})")
                    import time
                    time.sleep(2)
                    continue
                raise Exception("Embedding API 调用超时，已重试多次")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code in [429, 503, 502, 504]:  # 限流或服务器错误
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 2
                        current_app.logger.warning(
                            f"Embedding API 返回 {e.response.status_code}，等待 {wait_time} 秒后重试"
                        )
                        import time
                        time.sleep(wait_time)
                        continue
                last_error = f"Embedding API 调用失败: HTTP {e.response.status_code} - {e.response.text[:200]}"
            except requests.exceptions.RequestException as e:
                last_error = f"Embedding API 调用失败: {str(e)}"
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2)
                    continue
            except Exception as e:
                last_error = f"Embedding 处理错误: {str(e)}"
                break
        
        # 所有重试都失败
        raise Exception(last_error or "Embedding API 调用失败")
    
    def batch_generate_embeddings(self, texts: List[str], max_retries: int = 3) -> List[List[float]]:
        """
        批量生成向量（带重试机制）
        
        Args:
            texts: 文本列表
            max_retries: 最大重试次数
            
        Returns:
            向量列表
        """
        if not texts:
            return []
        
        url = f"{self.api_base_url}/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.model_name,
            "input": texts
        }
        
        last_error = None
        for attempt in range(max_retries):
            try:
                response = requests.post(url, json=data, headers=headers, timeout=60)
                
                # 处理 503 等服务器错误
                if response.status_code == 503:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 3  # 批量请求等待时间更长
                        current_app.logger.warning(
                            f"批量 Embedding API 返回 503，等待 {wait_time} 秒后重试 (尝试 {attempt + 1}/{max_retries})"
                        )
                        import time
                        time.sleep(wait_time)
                        continue
                    else:
                        raise Exception(f"批量 Embedding API 服务不可用 (503)，已重试 {max_retries} 次")
                
                response.raise_for_status()
                result = response.json()
                # 按输入顺序返回向量
                embeddings = [item['embedding'] for item in sorted(result['data'], key=lambda x: x['index'])]
                # 自动检测并更新维度（使用第一个向量的维度）
                if embeddings and len(embeddings) > 0:
                    actual_dimension = len(embeddings[0])
                    if self.dimension != actual_dimension:
                        current_app.logger.info(f"检测到向量维度: {actual_dimension} (模型: {self.model_name})")
                        self.dimension = actual_dimension
                return embeddings
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    current_app.logger.warning(f"批量 Embedding API 调用超时，重试中 (尝试 {attempt + 1}/{max_retries})")
                    import time
                    time.sleep(3)
                    continue
                raise Exception("批量 Embedding API 调用超时，已重试多次")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code in [429, 503, 502, 504]:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 3
                        current_app.logger.warning(
                            f"批量 Embedding API 返回 {e.response.status_code}，等待 {wait_time} 秒后重试"
                        )
                        import time
                        time.sleep(wait_time)
                        continue
                last_error = f"批量 Embedding API 调用失败: HTTP {e.response.status_code} - {e.response.text[:200]}"
            except requests.exceptions.RequestException as e:
                last_error = f"批量 Embedding API 调用失败: {str(e)}"
                if attempt < max_retries - 1:
                    import time
                    time.sleep(3)
                    continue
            except Exception as e:
                last_error = f"批量 Embedding 处理错误: {str(e)}"
                break
        
        raise Exception(last_error or "批量 Embedding API 调用失败")


class QdrantVectorStore:
    """Qdrant 向量存储客户端"""
    
    COLLECTION_NAME = "websites"
    
    def __init__(self, qdrant_url: str = "http://localhost:6333", vector_dimension: int = 1024):
        """
        初始化 Qdrant 客户端
        
        Args:
            qdrant_url: Qdrant 服务地址
            vector_dimension: 向量维度（会在首次使用时自动检测）
        """
        # 在 Docker 环境中，如果 URL 是 localhost，自动转换为服务名
        qdrant_url = self._normalize_qdrant_url(qdrant_url)
        self.client = QdrantClient(url=qdrant_url)
        self.vector_dimension = vector_dimension
        self._ensure_collection()
    
    def _normalize_qdrant_url(self, url: str) -> str:
        """
        规范化 Qdrant URL，在 Docker 环境中自动转换 localhost 为服务名
        
        Args:
            url: 原始 Qdrant URL
            
        Returns:
            规范化后的 URL
        """
        if not url:
            return url
        
        # 检测是否在 Docker 环境中
        is_docker = False
        try:
            import os
            # 方法1: 检查 /.dockerenv 文件
            if os.path.exists('/.dockerenv'):
                is_docker = True
            # 方法2: 检查 cgroup
            elif os.path.exists('/proc/self/cgroup'):
                with open('/proc/self/cgroup', 'r') as f:
                    if 'docker' in f.read():
                        is_docker = True
        except Exception:
            pass  # 如果检测失败，默认不转换
        
        # 如果在 Docker 环境中且 URL 包含 localhost，转换为服务名
        if is_docker and 'localhost' in url:
            url = url.replace('localhost', 'qdrant')
            current_app.logger.info(f"检测到 Docker 环境，已将 Qdrant URL 从 localhost 转换为服务名: {url}")
        
        return url
    
    def _ensure_collection(self, force_recreate: bool = False):
        """
        确保集合存在，如果维度不匹配则重新创建
        
        Args:
            force_recreate: 是否强制重新创建集合
        """
        try:
            collections = self.client.get_collections().collections
            collection_names = [c.name for c in collections]
            
            if self.COLLECTION_NAME in collection_names:
                # 集合已存在，检查维度是否匹配
                try:
                    collection_info = self.client.get_collection(self.COLLECTION_NAME)
                    existing_dimension = collection_info.config.params.vectors.size
                    
                    if existing_dimension != self.vector_dimension or force_recreate:
                        current_app.logger.warning(
                            f"集合维度不匹配: 现有={existing_dimension}, 需要={self.vector_dimension}，将重新创建集合"
                        )
                        # 删除旧集合
                        self.client.delete_collection(self.COLLECTION_NAME)
                        current_app.logger.info(f"已删除旧集合: {self.COLLECTION_NAME}")
                        # 重新创建
                        self._create_collection()
                    else:
                        current_app.logger.info(f"集合已存在，维度匹配: {self.vector_dimension}")
                except Exception as e:
                    # Qdrant版本兼容性问题，如果集合存在但无法读取配置，假设集合可用，不重新创建
                    error_str = str(e)
                    if "validation errors" in error_str or "pydantic" in error_str.lower() or "ParsingModel" in error_str:
                        current_app.logger.warning(f"Qdrant版本兼容性问题，跳过集合检查（集合可能正常可用）: {error_str[:150]}")
                        # 假设集合可用，不重新创建，避免每次搜索都重建集合
                        return
                    else:
                        # 其他错误，尝试重新创建
                        current_app.logger.warning(f"检查集合信息失败: {str(e)}，将重新创建")
                        try:
                            self.client.delete_collection(self.COLLECTION_NAME)
                        except:
                            pass
                        self._create_collection()
            else:
                # 集合不存在，创建新集合
                self._create_collection()
                
        except Exception as e:
            current_app.logger.error(f"初始化 Qdrant 集合失败: {str(e)}")
            raise
    
    def _create_collection(self):
        """创建集合"""
        self.client.create_collection(
            collection_name=self.COLLECTION_NAME,
            vectors_config=VectorParams(
                size=self.vector_dimension,
                distance=Distance.COSINE
            )
        )
        current_app.logger.info(f"创建 Qdrant 集合: {self.COLLECTION_NAME} (维度: {self.vector_dimension})")
    
    def update_dimension(self, new_dimension: int):
        """
        更新向量维度（如果维度改变，需要重新创建集合）
        
        Args:
            new_dimension: 新的向量维度
        """
        if self.vector_dimension != new_dimension:
            current_app.logger.info(f"更新向量维度: {self.vector_dimension} -> {new_dimension}")
            self.vector_dimension = new_dimension
            self._ensure_collection(force_recreate=True)
    
    def store_vector(self, website_id: int, vector: List[float], payload: Dict):
        """
        存储网站向量
        
        Args:
            website_id: 网站ID
            vector: 向量
            payload: 元数据（title, description, category等）
        """
        try:
            point = PointStruct(
                id=website_id,
                vector=vector,
                payload=payload
            )
            self.client.upsert(
                collection_name=self.COLLECTION_NAME,
                points=[point]
            )
        except Exception as e:
            current_app.logger.error(f"存储向量失败 (website_id={website_id}): {str(e)}")
            raise
    
    def search_similar(self, query_vector: List[float], limit: int = 20, 
                       user_id: Optional[int] = None, threshold: float = 0.3) -> List[Dict]:
        """
        搜索相似向量
        
        Args:
            query_vector: 查询向量
            limit: 返回数量
            user_id: 用户ID（用于权限过滤）
            threshold: 相似度阈值
            
        Returns:
            搜索结果列表，每个结果包含 website_id, score, payload
        """
        try:
            # 构建过滤条件（如果需要权限过滤）
            query_filter = None
            if user_id is not None:
                # 这里可以根据需要添加权限过滤
                # 例如：只搜索公开的网站或用户有权限的网站
                pass
            
            search_result = self.client.search(
                collection_name=self.COLLECTION_NAME,
                query_vector=query_vector,
                limit=limit,
                score_threshold=threshold,
                query_filter=query_filter
            )
            
            results = []
            for hit in search_result:
                results.append({
                    'website_id': hit.id,
                    'score': hit.score,
                    'payload': hit.payload
                })
            
            return results
        except Exception as e:
            current_app.logger.error(f"向量搜索失败: {str(e)}")
            raise
    
    def delete_vector(self, website_id: int):
        """
        删除向量
        
        Args:
            website_id: 网站ID
        """
        try:
            self.client.delete(
                collection_name=self.COLLECTION_NAME,
                points_selector=[website_id]
            )
        except Exception as e:
            current_app.logger.error(f"删除向量失败 (website_id={website_id}): {str(e)}")
            # 删除失败不算严重错误，只记录日志
            current_app.logger.warning(f"删除向量警告: {str(e)}")


class VectorSearchService:
    """向量搜索服务（整合 Embedding 和 Qdrant）"""
    
    def __init__(self, embedding_client: EmbeddingClient, vector_store: QdrantVectorStore):
        """
        初始化向量搜索服务
        
        Args:
            embedding_client: Embedding 客户端
            vector_store: 向量存储客户端
        """
        self.embedding_client = embedding_client
        self.vector_store = vector_store
        # 同步向量维度
        self.vector_store.vector_dimension = self.embedding_client.dimension
        self.vector_store._ensure_collection()
    
    def index_website(self, website_id: int, title: str, description: str, 
                     category_name: str = "", url: str = "") -> bool:
        """
        为网站生成向量并索引
        
        Args:
            website_id: 网站ID
            title: 网站标题
            description: 网站描述
            category_name: 分类名称
            url: 网站URL
            
        Returns:
            是否成功
        """
        try:
            # 构建搜索文本（标题 + 描述 + 分类）
            search_text = f"{title} {description} {category_name}".strip()
            
            if not search_text:
                current_app.logger.warning(f"网站 {website_id} 没有可索引的文本内容")
                return False
            
            # 生成向量
            vector = self.embedding_client.generate_embedding(search_text)
            
            # 检测到维度变化时，更新 Qdrant 集合
            if self.embedding_client.dimension != self.vector_store.vector_dimension:
                current_app.logger.info(
                    f"检测到向量维度变化: {self.vector_store.vector_dimension} -> {self.embedding_client.dimension}"
                )
                self.vector_store.update_dimension(self.embedding_client.dimension)
            
            # 构建元数据
            payload = {
                "title": title,
                "description": description or "",
                "category": category_name or "",
                "url": url or ""
            }
            
            # 存储到 Qdrant
            self.vector_store.store_vector(website_id, vector, payload)
            
            return True
        except Exception as e:
            current_app.logger.error(f"索引网站失败 (website_id={website_id}): {str(e)}")
            return False
    
    def search(self, query: str, limit: int = 20, user_id: Optional[int] = None, 
               threshold: float = 0.3, use_cache: bool = True) -> List[Dict]:
        """
        搜索相似网站（支持向量缓存）
        
        Args:
            query: 搜索查询
            limit: 返回数量
            user_id: 用户ID
            threshold: 相似度阈值
            use_cache: 是否使用向量缓存
            
        Returns:
            搜索结果列表
        """
        try:
            # 将查询文本转换为向量（使用缓存）
            query_vector = self.embedding_client.generate_embedding(query, use_cache=use_cache)
            
            # 在 Qdrant 中搜索
            results = self.vector_store.search_similar(
                query_vector=query_vector,
                limit=limit,
                user_id=user_id,
                threshold=threshold
            )
            
            return results
        except Exception as e:
            current_app.logger.error(f"向量搜索失败: {str(e)}")
            raise

