#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 搜索服务工具类"""

import json
import requests
import re
from typing import Dict, List, Optional
from flask import current_app

# AI 搜索提示词模板
AI_SEARCH_PROMPT_TEMPLATE = """你是一个专业的网站导航助手。用户想要搜索网站，请根据用户的搜索查询，理解其真实意图，并提取关键信息。

用户搜索查询：{user_query}

请完成以下任务：
1. 理解用户的搜索意图（例如：想要找什么类型的网站、用途是什么）
2. 提取关键词（包括同义词、相关词）
3. 分析可能的搜索场景（例如：学习、工作、娱乐等）

请以JSON格式返回结果：
{{
    "intent": "用户的搜索意图描述",
    "keywords": ["关键词1", "关键词2", "关键词3"],
    "related_terms": ["相关词1", "相关词2"],
    "category_hints": ["可能的分类1", "可能的分类2"],
    "search_type": "exact|fuzzy|semantic"
}}

只返回JSON，不要其他内容。"""

AI_SEARCH_RECOMMEND_PROMPT_TEMPLATE = """你是一个专业的网站导航助手。你拥有一个包含所有可用网站的完整数据库。请基于用户查询，从所有网站中推荐最相关的网站。

用户搜索查询：{user_query}
用户意图：{intent}

所有可用网站列表（共{total_count}个）：
{websites_list}

请仔细分析用户查询和每个网站的相关性，考虑：
1. 网站标题是否匹配用户需求
2. 网站描述是否相关
3. 网站分类是否相关
4. 语义相关性（即使没有完全匹配的关键词）
5. 向量相似度分数（如果提供）

请返回最相关的网站（最多{max_recommendations}个），按相关性从高到低排序。

请以JSON格式返回：
{{
    "recommendations": [
        {{
            "website_id": 1,
            "relevance_score": 0.95,
            "reason": "推荐理由（说明为什么这个网站与用户查询相关）"
        }}
    ],
    "summary": "搜索总结（简要说明找到了什么类型的网站）"
}}

只返回JSON，不要其他内容。"""


class AISearchService:
    """AI 搜索服务类"""
    
    def __init__(self, api_base_url: str, api_key: str, model_name: str, 
                 temperature: float = 0.7, max_tokens: int = 500):
        """
        初始化 AI 搜索服务
        
        Args:
            api_base_url: API 基础 URL
            api_key: API 密钥
            model_name: 模型名称
            temperature: 温度参数（0-1）
            max_tokens: 最大 token 数
        """
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = float(temperature) if temperature else 0.7
        self.max_tokens = max_tokens or 500
    
    def _call_api(self, messages: list, temperature: float = None, max_tokens: int = None) -> dict:
        """
        调用 AI API
        
        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大 token 数
            
        Returns:
            API 响应结果
        """
        url = f"{self.api_base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens
        }
        
        try:
            response = requests.post(url, json=data, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            raise Exception("AI API 调用超时，请稍后重试")
        except requests.exceptions.RequestException as e:
            raise Exception(f"AI API 调用失败: {str(e)}")
        except Exception as e:
            raise Exception(f"AI API 处理错误: {str(e)}")
    
    def _parse_json_response(self, content: str) -> dict:
        """
        解析 AI 返回的 JSON 响应
        
        Args:
            content: AI 返回的内容
            
        Returns:
            解析后的 JSON 字典
        """
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取 JSON 部分
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except:
                    pass
            raise Exception("无法解析 AI 响应，返回格式不正确")
    
    def analyze_search_intent(self, user_query: str) -> dict:
        """
        分析用户搜索意图
        
        Args:
            user_query: 用户搜索查询
            
        Returns:
            包含意图、关键词等的字典
        """
        prompt = AI_SEARCH_PROMPT_TEMPLATE.format(user_query=user_query)
        messages = [
            {"role": "system", "content": "你是一个专业的网站搜索助手，擅长理解用户搜索意图并提取关键词。"},
            {"role": "user", "content": prompt}
        ]
        
        try:
            response = self._call_api(messages, max_tokens=300)
            content = response['choices'][0]['message']['content']
            intent_result = self._parse_json_response(content)
            return intent_result
        except Exception as e:
            current_app.logger.error(f"AI 意图分析失败: {str(e)}")
            raise
    
    def recommend_websites(self, user_query: str, intent: dict, websites: List[dict], 
                          vector_scores: Optional[Dict[int, float]] = None,
                          max_recommendations: int = 20) -> dict:
        """
        基于 AI 推荐网站（从所有网站中智能推荐）
        
        Args:
            user_query: 用户搜索查询
            intent: 意图分析结果
            websites: 所有可用网站列表（字典格式）
            vector_scores: 向量搜索的相似度分数字典 {website_id: score}
            max_recommendations: 最大推荐数量
            
        Returns:
            推荐结果
        """
        # 为网站添加向量相似度分数（如果提供）
        websites_with_scores = []
        for w in websites:
            website_dict = w.copy() if isinstance(w, dict) else {
                'id': w.id if hasattr(w, 'id') else w.get('id'),
                'title': w.title if hasattr(w, 'title') else w.get('title', ''),
                'description': w.description if hasattr(w, 'description') else w.get('description', ''),
                'category': w.category.name if hasattr(w, 'category') and w.category else w.get('category', ''),
                'url': w.url if hasattr(w, 'url') else w.get('url', '')
            }
            if vector_scores and website_dict.get('id') in vector_scores:
                website_dict['vector_score'] = vector_scores[website_dict['id']]
            websites_with_scores.append(website_dict)
        
        # 限制传给AI的网站数量，避免token过多（但尽量多传一些，让AI有更多选择）
        # 优先选择有向量分数的网站（向量搜索的结果）
        if vector_scores:
            # 有向量分数的网站优先
            websites_with_vector = [w for w in websites_with_scores if w.get('vector_score') is not None]
            websites_without_vector = [w for w in websites_with_scores if w.get('vector_score') is None]
            # 按向量分数排序
            websites_with_vector.sort(key=lambda x: x.get('vector_score', 0), reverse=True)
            websites_for_ai = websites_with_vector[:150] + websites_without_vector[:50]
        else:
            # 没有向量搜索，优先选择有描述的网站
            websites_with_desc = [w for w in websites_with_scores if w.get('description')]
            websites_without_desc = [w for w in websites_with_scores if not w.get('description')]
            websites_for_ai = (websites_with_desc[:150] + websites_without_desc[:50])[:200]
        
        websites_for_ai = websites_for_ai[:200]  # 最多200个
        
        prompt = AI_SEARCH_RECOMMEND_PROMPT_TEMPLATE.format(
            user_query=user_query,
            intent=intent.get('intent', ''),
            total_count=len(websites),
            websites_list=json.dumps(websites_for_ai, ensure_ascii=False, indent=2),
            max_recommendations=max_recommendations
        )
        
        messages = [
            {"role": "system", "content": "你是一个专业的网站推荐助手，能够准确评估网站与用户查询的相关性。"},
            {"role": "user", "content": prompt}
        ]
        
        try:
            response = self._call_api(messages, max_tokens=2000)  # 增加token数，因为要处理更多网站
            content = response['choices'][0]['message']['content']
            return self._parse_json_response(content)
        except Exception as e:
            current_app.logger.error(f"AI 推荐失败: {str(e)}")
            raise


def create_ai_service_from_settings(settings) -> Optional[AISearchService]:
    """
    从站点设置创建 AI 搜索服务
    
    Args:
        settings: SiteSettings 对象
        
    Returns:
        AISearchService 实例，如果配置不完整则返回 None
    """
    if not settings.ai_search_enabled:
        return None
    
    if not all([settings.ai_api_base_url, settings.ai_api_key, settings.ai_model_name]):
        return None
    
    try:
        return AISearchService(
            api_base_url=settings.ai_api_base_url,
            api_key=settings.ai_api_key,
            model_name=settings.ai_model_name,
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens
        )
    except Exception as e:
        current_app.logger.error(f"创建 AI 服务失败: {str(e)}")
        return None

