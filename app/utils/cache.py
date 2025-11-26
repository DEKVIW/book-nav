#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""简单的内存缓存工具（用于搜索优化）"""

import time
import hashlib
from typing import Any, Optional, Dict
from threading import Lock
from flask import current_app


class SimpleCache:
    """简单的内存缓存实现（线程安全）"""
    
    def __init__(self, default_ttl: int = 3600):
        """
        初始化缓存
        
        Args:
            default_ttl: 默认过期时间（秒），默认1小时
        """
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()
        self.default_ttl = default_ttl
        self._max_size = 1000  # 最大缓存条目数
    
    def _make_key(self, prefix: str, *args, **kwargs) -> str:
        """生成缓存键"""
        # 将参数序列化为字符串
        key_parts = [prefix]
        if args:
            key_parts.extend(str(arg) for arg in args)
        if kwargs:
            sorted_kwargs = sorted(kwargs.items())
            key_parts.extend(f"{k}={v}" for k, v in sorted_kwargs)
        
        key_str = "|".join(key_parts)
        # 使用hash缩短键长度
        return hashlib.md5(key_str.encode('utf-8')).hexdigest()
    
    def get(self, key: str) -> Optional[Any]:
        """
        获取缓存值
        
        Args:
            key: 缓存键
            
        Returns:
            缓存值，如果不存在或已过期则返回None
        """
        with self._lock:
            if key not in self._cache:
                return None
            
            entry = self._cache[key]
            # 检查是否过期
            if time.time() > entry['expires_at']:
                del self._cache[key]
                return None
            
            return entry['value']
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        设置缓存值
        
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒），如果为None则使用默认值
        """
        with self._lock:
            # 如果缓存已满，删除最旧的条目
            if len(self._cache) >= self._max_size:
                self._evict_oldest()
            
            ttl = ttl or self.default_ttl
            self._cache[key] = {
                'value': value,
                'expires_at': time.time() + ttl,
                'created_at': time.time()
            }
    
    def _evict_oldest(self) -> None:
        """删除最旧的缓存条目"""
        if not self._cache:
            return
        
        # 找到最旧的条目
        oldest_key = min(
            self._cache.keys(),
            key=lambda k: self._cache[k]['created_at']
        )
        del self._cache[oldest_key]
    
    def delete(self, key: str) -> None:
        """删除缓存条目"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
    
    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
    
    def stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        with self._lock:
            now = time.time()
            valid_count = sum(
                1 for entry in self._cache.values()
                if entry['expires_at'] > now
            )
            expired_count = len(self._cache) - valid_count
            
            return {
                'total_entries': len(self._cache),
                'valid_entries': valid_count,
                'expired_entries': expired_count,
                'max_size': self._max_size
            }


# 全局缓存实例
_search_cache = SimpleCache(default_ttl=3600)  # 搜索结果缓存1小时
_vector_cache = SimpleCache(default_ttl=86400)  # 向量缓存24小时（embedding结果不变）


def get_search_cache_key(query: str, use_ai: bool, user_id: Optional[int] = None) -> str:
    """
    生成搜索缓存键
    
    Args:
        query: 搜索查询
        use_ai: 是否使用AI
        user_id: 用户ID（用于区分不同用户的权限）
        
    Returns:
        缓存键
    """
    cache = SimpleCache()
    return cache._make_key('search', query=query.lower().strip(), use_ai=use_ai, user_id=user_id)


def get_vector_cache_key(query: str, model: str) -> str:
    """
    生成向量缓存键
    
    Args:
        query: 搜索查询
        model: embedding模型名称
        
    Returns:
        缓存键
    """
    cache = SimpleCache()
    return cache._make_key('vector', query=query.lower().strip(), model=model)


def cache_search_result(query: str, use_ai: bool, result: Any, user_id: Optional[int] = None, ttl: int = 3600) -> None:
    """
    缓存搜索结果
    
    Args:
        query: 搜索查询
        use_ai: 是否使用AI
        result: 搜索结果
        user_id: 用户ID
        ttl: 缓存时间（秒）
    """
    key = get_search_cache_key(query, use_ai, user_id)
    _search_cache.set(key, result, ttl=ttl)


def get_cached_search_result(query: str, use_ai: bool, user_id: Optional[int] = None) -> Optional[Any]:
    """
    获取缓存的搜索结果
    
    Args:
        query: 搜索查询
        use_ai: 是否使用AI
        user_id: 用户ID
        
    Returns:
        缓存的搜索结果，如果不存在则返回None
    """
    key = get_search_cache_key(query, use_ai, user_id)
    return _search_cache.get(key)


def cache_vector(query: str, model: str, vector: list, ttl: int = 86400) -> None:
    """
    缓存查询向量
    
    Args:
        query: 搜索查询
        model: embedding模型名称
        vector: 向量列表
        ttl: 缓存时间（秒）
    """
    key = get_vector_cache_key(query, model)
    _vector_cache.set(key, vector, ttl=ttl)


def get_cached_vector(query: str, model: str) -> Optional[list]:
    """
    获取缓存的查询向量
    
    Args:
        query: 搜索查询
        model: embedding模型名称
        
    Returns:
        缓存的向量，如果不存在则返回None
    """
    key = get_vector_cache_key(query, model)
    return _vector_cache.get(key)


def clear_search_cache() -> None:
    """清空搜索结果缓存"""
    _search_cache.clear()


def clear_vector_cache() -> None:
    """清空向量缓存"""
    _vector_cache.clear()


def get_cache_stats() -> Dict[str, Any]:
    """获取缓存统计信息"""
    return {
        'search_cache': _search_cache.stats(),
        'vector_cache': _vector_cache.stats()
    }

