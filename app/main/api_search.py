#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""搜索相关API路由"""

from flask import request, jsonify, Response, stream_with_context, current_app
from flask_login import current_user, login_required
from app.main import bp
from app.models import Website, SiteSettings, Category
from sqlalchemy import or_
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import time
import json as json_module
import sys


@bp.route('/api/search')
def api_search():
    """搜索API（支持AI搜索和传统搜索）"""
    query = request.args.get('q', '')
    use_ai = request.args.get('ai', 'false').lower() == 'true'
    progressive = request.args.get('progressive', 'false').lower() == 'true'
    
    if not query:
        return jsonify({"websites": []})
    
    user_id = current_user.id if current_user.is_authenticated else None
    if not progressive:
        try:
            from app.utils.cache import get_cached_search_result, cache_search_result
            cache_enabled = not use_ai or len(query) <= 5
            if cache_enabled:
                cached_result = get_cached_search_result(query, use_ai, user_id)
                if cached_result:
                    return jsonify(cached_result)
        except Exception as e:
            current_app.logger.warning(f"缓存检查失败: {str(e)}")
    
    settings = SiteSettings.get_settings()
    
    base_query = Website.query
    if not current_user.is_authenticated:
        base_query = base_query.filter_by(is_private=False)
    elif not current_user.is_admin:
        base_query = base_query.filter(
            (Website.is_private == False) |
            (Website.created_by_id == current_user.id) |
            (Website.visible_to.contains(str(current_user.id)))
        )
    
    if progressive and use_ai and settings.ai_search_enabled:
        return _progressive_search(query, user_id)
    
    if use_ai and settings.ai_search_enabled:
        try:
            from app.utils.ai_search import AISearchService
            
            if all([settings.ai_api_base_url, settings.ai_api_key, settings.ai_model_name]):
                ai_service = AISearchService(
                    api_base_url=settings.ai_api_base_url,
                    api_key=settings.ai_api_key,
                    model_name=settings.ai_model_name,
                    temperature=settings.ai_temperature,
                    max_tokens=settings.ai_max_tokens
                )
                
                needs_ai_intent = (
                    len(query) > 5 or
                    any(word in query for word in ['怎么', '如何', '哪里', '为什么', '什么', '哪个']) or
                    ' ' in query
                )
                
                candidate_sites = set()
                vector_scores = {}
                intent = None
                keyword_results = []
                
                def do_vector_search():
                    """向量搜索任务"""
                    if not (settings.vector_search_enabled and all([settings.qdrant_url, settings.embedding_model])):
                        return [], {}
                    
                    try:
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
                        
                        vector_search_results = vector_service.search(
                            query=query,
                            limit=settings.vector_max_results or 50,
                            user_id=current_user.id if current_user.is_authenticated else None,
                            threshold=settings.vector_similarity_threshold or 0.3
                        )
                        
                        site_ids = [r['website_id'] for r in vector_search_results]
                        scores = {r['website_id']: r['score'] for r in vector_search_results}
                        return site_ids, scores
                    except Exception as e:
                        current_app.logger.warning(f"向量搜索失败: {str(e)}")
                        return [], {}
                
                def do_keyword_search():
                    """关键词搜索任务"""
                    keyword_query = base_query.filter(
                        or_(
                            Website.title.ilike(f'%{query}%'),
                            Website.description.ilike(f'%{query}%'),
                            Website.url.ilike(f'%{query}%')
                        )
                    )
                    results = keyword_query.limit(200).all()
                    return results
                
                def do_ai_intent():
                    """AI意图理解任务（仅在需要时执行）"""
                    if not needs_ai_intent:
                        return None
                    try:
                        result = ai_service.analyze_search_intent(query)
                        return result
                    except Exception as e:
                        current_app.logger.warning(f"AI意图理解失败: {str(e)}")
                        return None
                
                with ThreadPoolExecutor(max_workers=3) as executor:
                    future_vector = executor.submit(do_vector_search)
                    future_keyword = executor.submit(do_keyword_search)
                    future_intent = executor.submit(do_ai_intent) if needs_ai_intent else None
                    
                    vector_ids, vector_scores = future_vector.result()
                    keyword_results = future_keyword.result()
                    if future_intent:
                        intent = future_intent.result()
                
                candidate_sites.update(vector_ids)
                for site in keyword_results:
                    candidate_sites.add(site.id)
                
                if intent:
                    if intent.get('keywords'):
                        expanded_keywords = intent['keywords']
                        for keyword in expanded_keywords[:5]:
                            expanded_query = base_query.filter(
                                or_(
                                    Website.title.ilike(f'%{keyword}%'),
                                    Website.description.ilike(f'%{keyword}%')
                                )
                            )
                            for site in expanded_query.limit(100).all():
                                candidate_sites.add(site.id)
                    
                    if intent.get('related_terms'):
                        for term in intent['related_terms'][:3]:
                            related_query = base_query.filter(
                                or_(
                                    Website.title.ilike(f'%{term}%'),
                                    Website.description.ilike(f'%{term}%')
                                )
                            )
                            for site in related_query.limit(50).all():
                                candidate_sites.add(site.id)
                
                candidate_ids = list(candidate_sites)[:400]
                candidate_websites = base_query.filter(Website.id.in_(candidate_ids)).all()
                
                websites_for_ai = []
                website_id_map = {}
                
                for site in candidate_websites:
                    website_id_map[site.id] = site
                    websites_for_ai.append({
                        'id': site.id,
                        'title': site.title,
                        'description': site.description or '',
                        'category': site.category.name if site.category else '',
                        'url': site.url
                    })
                
                if not intent:
                    intent = {
                        'intent': f"用户想要查找与'{query}'相关的网站",
                        'keywords': [query],
                        'related_terms': [],
                        'category_hints': []
                    }
                
                recommendations = ai_service.recommend_websites(
                    query, 
                    intent, 
                    websites_for_ai,
                    vector_scores=vector_scores if vector_scores else None,
                    max_recommendations=20
                )
                ai_summary = recommendations.get('summary')
                
                ai_results = []
                if recommendations and recommendations.get('recommendations'):
                    recommended_ids = [rec['website_id'] for rec in recommendations['recommendations']]
                    ai_results = [website_id_map[wid] for wid in recommended_ids if wid in website_id_map]
                
                websites_data = []
                for site in ai_results:
                    category_data = None
                    if site.category:
                        category_data = {
                            'id': site.category.id,
                            'name': site.category.name,
                            'icon': site.category.icon
                        }
                    
                    websites_data.append({
                        'id': site.id,
                        'title': site.title,
                        'description': site.description,
                        'url': site.url,
                        'icon': site.icon,
                        'category': category_data,
                        'views': site.views,
                        'is_private': site.is_private
                    })
                
                result = {
                    "websites": websites_data,
                    "ai_enabled": True,
                    "ai_summary": ai_summary,
                    "total": len(websites_data)
                }
                
                if len(query) <= 5:
                    try:
                        from app.utils.cache import cache_search_result
                        cache_search_result(query, use_ai, result, user_id, ttl=3600)
                    except Exception as e:
                        current_app.logger.warning(f"缓存搜索结果失败: {str(e)}")
                
                return jsonify(result)
                    
        except Exception as e:
            current_app.logger.error(f"AI 搜索失败: {str(e)}")
    
    websites_query = base_query.filter(
        or_(
            Website.title.ilike(f'%{query}%'),
            Website.description.ilike(f'%{query}%'),
            Website.url.ilike(f'%{query}%')
        )
    )
    
    traditional_results = websites_query.all()
    
    websites_data = []
    for site in traditional_results:
        category_data = None
        if site.category:
            category_data = {
                'id': site.category.id,
                'name': site.category.name,
                'icon': site.category.icon
            }
        
        websites_data.append({
            'id': site.id,
            'title': site.title,
            'description': site.description,
            'url': site.url,
            'icon': site.icon,
            'category': category_data,
            'views': site.views,
            'is_private': site.is_private
        })
    
    result = {
        "websites": websites_data,
        "ai_enabled": False,
        "total": len(websites_data)
    }
    
    try:
        from app.utils.cache import cache_search_result
        cache_search_result(query, use_ai, result, user_id, ttl=3600)
    except Exception as e:
        current_app.logger.warning(f"缓存搜索结果失败: {str(e)}")
    
    return jsonify(result)


def _progressive_search(query: str, user_id: Optional[int]):
    """渐进式搜索：分阶段返回结果"""
    settings = SiteSettings.get_settings()
    
    base_query = Website.query
    if not current_user.is_authenticated:
        base_query = base_query.filter_by(is_private=False)
    elif not current_user.is_admin:
        base_query = base_query.filter(
            (Website.is_private == False) |
            (Website.created_by_id == current_user.id) |
            (Website.visible_to.contains(str(current_user.id)))
        )
    
    def generate():
        try:
            # 阶段1: 立即返回关键词搜索结果（最快，不等待其他任务）
            keyword_query = base_query.filter(
                or_(
                    Website.title.ilike(f'%{query}%'),
                    Website.description.ilike(f'%{query}%'),
                    Website.url.ilike(f'%{query}%')
                )
            )
            keyword_results = keyword_query.limit(20).all()
            
            websites_data = []
            for site in keyword_results:
                category_data = None
                if site.category:
                    category_data = {
                        'id': site.category.id,
                        'name': site.category.name,
                        'icon': site.category.icon
                    }
                
                websites_data.append({
                    'id': site.id,
                    'title': site.title,
                    'description': site.description,
                    'url': site.url,
                    'icon': site.icon,
                    'category': category_data,
                    'views': site.views,
                    'is_private': site.is_private
                })
            
            # 立即发送第一阶段结果，不等待其他任务
            yield f"data: {json_module.dumps({
                'stage': 'initial',
                'websites': websites_data,
                'total': len(websites_data),
                'status': '关键词搜索结果已返回，正在补充向量搜索结果...'
            }, ensure_ascii=False)}\n\n"
            
            # 刷新输出缓冲区，确保数据立即发送到客户端
            sys.stdout.flush()
            
            if settings.ai_search_enabled and settings.vector_search_enabled and all([settings.qdrant_url, settings.embedding_model]):
                try:
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
                    
                    vector_search_results = vector_service.search(
                        query=query,
                        limit=settings.vector_max_results or 30,
                        user_id=user_id,
                        threshold=settings.vector_similarity_threshold or 0.3
                    )
                    
                    existing_ids = {site['id'] for site in websites_data}
                    vector_ids = [r['website_id'] for r in vector_search_results if r['website_id'] not in existing_ids]
                    
                    if vector_ids:
                        vector_query = base_query.filter(Website.id.in_(vector_ids))
                        vector_results = vector_query.all()
                        
                        vector_websites = []
                        score_map = {r['website_id']: r['score'] for r in vector_search_results}
                        
                        for site in vector_results:
                            category_data = None
                            if site.category:
                                category_data = {
                                    'id': site.category.id,
                                    'name': site.category.name,
                                    'icon': site.category.icon
                                }
                            
                            vector_websites.append({
                                'id': site.id,
                                'title': site.title,
                                'description': site.description,
                                'url': site.url,
                                'icon': site.icon,
                                'category': category_data,
                                'views': site.views,
                                'is_private': site.is_private,
                                'vector_score': score_map.get(site.id, 0)
                            })
                        
                        # 按vector_score降序排序向量结果
                        vector_websites.sort(key=lambda x: x.get('vector_score', 0), reverse=True)
                        
                        # 分批返回向量搜索结果，实现真正的渐进式加载
                        # 使用小批次（3-4条），让用户更快看到结果，减少等待感
                        batch_size = 3  # 每批返回3条，前端会逐个显示（每个80ms，总共240ms）
                        total_batches = (len(vector_websites) + batch_size - 1) // batch_size
                        
                        # 保存初始的关键词搜索结果
                        initial_keyword_results = websites_data.copy()
                        
                        for batch_idx in range(total_batches):
                            end_idx = min((batch_idx + 1) * batch_size, len(vector_websites))
                            
                            # 合并：当前已处理的向量结果（从0到end_idx）+ 初始关键词结果
                            all_websites = vector_websites[:end_idx] + initial_keyword_results
                            
                            # 发送当前批次（累积结果）
                            yield f"data: {json_module.dumps({
                                'stage': 'enhanced',
                                'websites': all_websites,
                                'total': len(all_websites),
                                'status': f'向量搜索结果已补充 ({end_idx}/{len(vector_websites)})，正在AI优化排序...' if batch_idx < total_batches - 1 else '向量搜索结果已补充，正在AI优化排序...',
                                'is_batch': batch_idx < total_batches - 1  # 标记是否还有更多批次
                            }, ensure_ascii=False)}\n\n"
                            
                            sys.stdout.flush()
                            
                            # 如果不是最后一批，稍微延迟，让前端有时间逐个显示卡片
                            # 3条 * 80ms = 240ms，批次间隔设为350ms，确保前端有足够时间显示当前批次
                            # 这样用户每350ms就能看到新卡片，体验更流畅
                            if batch_idx < total_batches - 1:
                                time.sleep(0.35)  # 350ms延迟，让前端有时间逐个显示当前批次的所有卡片
                        
                        # 最终合并所有结果：向量结果在前，关键词结果在后
                        websites_data = vector_websites + initial_keyword_results
                    else:
                        yield f"data: {json_module.dumps({
                            'stage': 'enhanced',
                            'websites': websites_data,
                            'total': len(websites_data),
                            'status': '向量搜索未找到新结果'
                        }, ensure_ascii=False)}\n\n"
                        sys.stdout.flush()
                        
                except Exception as e:
                    current_app.logger.warning(f"向量搜索失败: {str(e)}")
                    yield f"data: {json_module.dumps({
                        'stage': 'enhanced',
                        'websites': websites_data,
                        'total': len(websites_data),
                        'status': f'向量搜索失败: {str(e)}'
                    }, ensure_ascii=False)}\n\n"
            
            if settings.ai_search_enabled and all([settings.ai_api_base_url, settings.ai_api_key, settings.ai_model_name]):
                if len(websites_data) > 0:
                    needs_ai_intent = (
                        len(query) > 5 or 
                        any(word in query for word in ['怎么', '如何', '哪里', '为什么', '什么', '哪个']) or
                        ' ' in query
                    )
                    try:
                        from app.utils.ai_search import AISearchService
                        
                        ai_service = AISearchService(
                            api_base_url=settings.ai_api_base_url,
                            api_key=settings.ai_api_key,
                            model_name=settings.ai_model_name,
                            temperature=settings.ai_temperature,
                            max_tokens=settings.ai_max_tokens
                        )
                        
                        websites_for_ai = []
                        website_id_map = {}
                        for site_data in websites_data[:50]:
                            websites_for_ai.append({
                                'id': site_data['id'],
                                'title': site_data['title'],
                                'description': site_data.get('description', ''),
                                'category': site_data.get('category', {}).get('name', ''),
                                'url': site_data['url'],
                                'vector_score': site_data.get('vector_score')
                            })
                            website_id_map[site_data['id']] = site_data
                        
                        if needs_ai_intent:
                            intent = ai_service.analyze_search_intent(query)
                        else:
                            intent = {
                                'intent': f"用户想要查找与'{query}'相关的网站",
                                'keywords': [query],
                                'related_terms': [],
                                'category_hints': []
                            }
                        
                        recommendations = ai_service.recommend_websites(
                            query,
                            intent,
                            websites_for_ai,
                            vector_scores={w['id']: w.get('vector_score', 0) for w in websites_for_ai if w.get('vector_score')},
                            max_recommendations=20
                        )
                        
                        if recommendations and recommendations.get('recommendations'):
                            recommended_ids = [rec['website_id'] for rec in recommendations['recommendations']]
                            ai_sorted_websites = []
                            for wid in recommended_ids:
                                if wid in website_id_map:
                                    ai_sorted_websites.append(website_id_map[wid])
                            
                            for site_data in websites_data:
                                if site_data['id'] not in recommended_ids:
                                    ai_sorted_websites.append(site_data)
                            
                            websites_data = ai_sorted_websites
                            ai_summary = recommendations.get('summary', '')
                        else:
                            ai_summary = None
                        
                        yield f"data: {json_module.dumps({
                            'stage': 'final',
                            'websites': websites_data,
                            'total': len(websites_data),
                            'ai_enabled': True,
                            'ai_summary': ai_summary,
                            'status': 'AI智能排序完成'
                        }, ensure_ascii=False)}\n\n"
                        
                        # 刷新输出缓冲区
                        sys.stdout.flush()
                    except Exception as e:
                        current_app.logger.error(f"AI排序失败: {str(e)}")
                        yield f"data: {json_module.dumps({
                            'stage': 'final',
                            'websites': websites_data,
                            'total': len(websites_data),
                            'status': f'AI排序失败，使用默认排序: {str(e)}'
                        }, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json_module.dumps({
                        'stage': 'final',
                        'websites': websites_data,
                        'total': len(websites_data),
                        'ai_enabled': False,
                        'status': '未找到相关网站'
                    }, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json_module.dumps({
                    'stage': 'final',
                    'websites': websites_data,
                    'total': len(websites_data),
                    'ai_enabled': False,
                    'status': '搜索完成'
                }, ensure_ascii=False)}\n\n"
                
        except Exception as e:
            current_app.logger.error(f"渐进式搜索失败: {str(e)}")
            yield f"data: {json_module.dumps({
                'stage': 'error',
                'error': str(e),
                'websites': [],
                'total': 0
            }, ensure_ascii=False)}\n\n"
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@bp.route('/api/cache/stats')
@login_required
def cache_stats():
    """获取缓存统计信息（仅管理员）"""
    if not current_user.is_admin:
        return jsonify({"error": "权限不足"}), 403
    
    try:
        from app.utils.cache import get_cache_stats
        stats = get_cache_stats()
        return jsonify({
            "success": True,
            "stats": stats
        })
    except Exception as e:
        current_app.logger.error(f"获取缓存统计失败: {str(e)}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/cache/clear', methods=['POST'])
@login_required
def clear_cache():
    """清空缓存（仅管理员）"""
    if not current_user.is_admin:
        return jsonify({"error": "权限不足"}), 403
    
    try:
        from app.utils.cache import clear_search_cache, clear_vector_cache
        cache_type = request.json.get('type', 'all') if request.is_json else 'all'
        
        if cache_type == 'search' or cache_type == 'all':
            clear_search_cache()
        if cache_type == 'vector' or cache_type == 'all':
            clear_vector_cache()
        
        return jsonify({
            "success": True,
            "message": f"已清空{cache_type}缓存"
        })
    except Exception as e:
        current_app.logger.error(f"清空缓存失败: {str(e)}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/category/<int:category_id>/search')
def search_in_category(category_id):
    """分类内搜索"""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"success": False, "message": "搜索关键词不能为空"})
    
    category = Category.query.get_or_404(category_id)
    
    websites = Website.query.filter(
        Website.category_id == category_id,
        or_(
            Website.title.ilike(f'%{query}%'),
            Website.description.ilike(f'%{query}%'),
            Website.url.ilike(f'%{query}%')
        )
    )
    
    if not current_user.is_authenticated:
        websites = websites.filter_by(is_private=False)
    elif not current_user.is_admin:
        websites = websites.filter(
            (Website.is_private == False) |
            (Website.created_by_id == current_user.id) |
            (Website.visible_to.contains(str(current_user.id)))
        )
    
    websites = websites.order_by(Website.sort_order.desc(), Website.created_at.asc(), Website.views.desc()).all()
    
    result = []
    for site in websites:
        result.append({
            'id': site.id,
            'title': site.title,
            'url': site.url,
            'description': site.description,
            'icon': site.icon,
            'sort_order': site.sort_order,
            'is_private': site.is_private
        })
    
    return jsonify({
        "success": True,
        "count": len(result),
        "keyword": query,
        "websites": result
    })

