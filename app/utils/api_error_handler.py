#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""API错误处理和分类工具类"""

import json
import re
from typing import Dict, Optional, Tuple
import requests


class APIErrorInfo:
    """API错误信息结构"""
    def __init__(self, error_type: str, reason: str, suggestion: str = ""):
        self.error_type = error_type
        self.reason = reason
        self.suggestion = suggestion
    
    def to_dict(self) -> Dict:
        """转换为字典格式"""
        result = {
            'error_type': self.error_type,
            'reason': self.reason
        }
        if self.suggestion:
            result['suggestion'] = self.suggestion
        return result
    
    def to_message(self) -> str:
        """转换为友好的错误消息"""
        msg = f"❌ {self.error_type}\n原因：{self.reason}"
        if self.suggestion:
            msg += f"\n建议：{self.suggestion}"
        return msg


def parse_api_error_response(response_text: str) -> Optional[Dict]:
    """
    解析API响应体中的错误信息（OpenAI兼容格式）
    
    Args:
        response_text: 响应体文本
        
    Returns:
        解析后的错误信息字典，如果解析失败返回None
    """
    if not response_text:
        return None
    
    try:
        error_data = json.loads(response_text)
        if isinstance(error_data, dict) and 'error' in error_data:
            error_obj = error_data['error']
            if isinstance(error_obj, dict):
                return {
                    'message': error_obj.get('message', ''),
                    'type': error_obj.get('type', ''),
                    'code': error_obj.get('code', '')
                }
    except (json.JSONDecodeError, AttributeError):
        pass
    
    return None


def classify_api_error(
    exception: Exception,
    api_type: str = 'ai',
    api_base_url: str = '',
    model_name: str = '',
    api_key: str = '',
    response: Optional[requests.Response] = None
) -> APIErrorInfo:
    """
    分类并返回友好的API错误信息
    
    Args:
        exception: 捕获的异常对象
        api_type: API类型 ('ai' 或 'embedding')
        api_base_url: API基础URL
        model_name: 模型名称
        api_key: API密钥（仅用于判断是否为空）
        response: HTTP响应对象（如果有）
        
    Returns:
        APIErrorInfo对象，包含错误类型、原因和建议
    """
    api_name = "AI API" if api_type == 'ai' else "Embedding API"
    
    # 1. 检查是否有HTTP响应，尝试解析响应体中的错误信息
    if response is not None:
        try:
            error_info = parse_api_error_response(response.text)
            if error_info:
                error_message = error_info.get('message', '').lower()
                error_type_code = error_info.get('type', '').lower()
                error_code = error_info.get('code', '').lower()
                
                # API Key相关错误
                if response.status_code == 401:
                    if 'invalid' in error_message and 'key' in error_message:
                        return APIErrorInfo(
                            error_type="API Key 验证失败",
                            reason="API Key 无效或已过期",
                            suggestion="请检查 API Key 是否正确，或前往 API 提供商处生成新的 Key"
                        )
                    elif 'unauthorized' in error_message:
                        return APIErrorInfo(
                            error_type="API Key 验证失败",
                            reason="API Key 未授权",
                            suggestion="请检查 API Key 是否有访问权限"
                        )
                    else:
                        return APIErrorInfo(
                            error_type="API Key 验证失败",
                            reason=error_info.get('message', 'API Key 无效'),
                            suggestion="请检查 API Key 是否正确"
                        )
                
                # 权限相关错误
                if response.status_code == 403:
                    if 'permission' in error_message or 'forbidden' in error_message:
                        return APIErrorInfo(
                            error_type="API Key 权限不足",
                            reason="当前 API Key 没有访问权限",
                            suggestion="请检查 API Key 是否有访问该 API 的权限"
                        )
                    else:
                        return APIErrorInfo(
                            error_type="访问被拒绝",
                            reason=error_info.get('message', '访问被拒绝'),
                            suggestion="请检查 API Key 权限或联系 API 提供商"
                        )
                
                # 模型相关错误
                if response.status_code == 404:
                    if 'model' in error_message:
                        return APIErrorInfo(
                            error_type="模型不存在",
                            reason=f"模型 '{model_name}' 在当前 API 中不存在",
                            suggestion="请检查模型名称是否正确，或查看 API 文档确认可用模型列表"
                        )
                    else:
                        return APIErrorInfo(
                            error_type="资源不存在",
                            reason=error_info.get('message', '请求的资源不存在'),
                            suggestion="请检查 API 基础 URL 和端点路径是否正确"
                        )
                
                # 模型不可用或参数错误（400状态码）
                if response.status_code == 400:
                    # 优先检查是否是模型相关错误
                    if 'model' in error_message:
                        return APIErrorInfo(
                            error_type="模型配置错误",
                            reason=f"模型 '{model_name}' 不可用或参数错误：{error_info.get('message', '')}",
                            suggestion="请检查模型名称是否正确，或查看 API 文档确认模型是否可用。如果其他模型可以正常使用，可能是该模型需要特殊权限或配置。"
                        )
                    # 检查是否是 API Key 过期但可能是模型权限问题
                    elif ('expired' in error_message and 'key' in error_message) or ('key expired' in error_message):
                        return APIErrorInfo(
                            error_type="API Key 或模型权限问题",
                            reason=f"API 返回错误：{error_info.get('message', '')}",
                            suggestion="如果其他模型可以正常使用，可能是该模型 '{model_name}' 需要特殊权限或该 API Key 无权访问此模型。请检查：1) 模型名称是否正确 2) API Key 是否有访问该模型的权限 3) 或尝试使用其他模型"
                        )
                    elif 'invalid' in error_message and 'key' in error_message:
                        return APIErrorInfo(
                            error_type="API Key 格式错误",
                            reason=f"API Key 格式错误：{error_info.get('message', '')}",
                            suggestion="请检查 API Key 格式是否正确"
                        )
                    else:
                        return APIErrorInfo(
                            error_type="请求参数错误",
                            reason=f"API 请求参数错误：{error_info.get('message', '')}",
                            suggestion="请检查模型名称、温度参数等配置是否正确。如果其他模型可以正常使用，建议尝试更换模型。"
                        )
                
                # 限流错误
                if response.status_code == 429:
                    return APIErrorInfo(
                        error_type="API 调用频率过高",
                        reason="已达到 API 调用频率限制",
                        suggestion="请稍后重试，或检查 API 配额和限流设置"
                    )
                
                # 服务器错误
                if response.status_code in [500, 502, 503, 504]:
                    return APIErrorInfo(
                        error_type="服务器错误",
                        reason=f"API 服务器返回错误 (HTTP {response.status_code})",
                        suggestion="服务器暂时不可用，请稍后重试"
                    )
                
                # 其他HTTP错误，使用API返回的错误消息
                return APIErrorInfo(
                    error_type=f"API 调用失败 (HTTP {response.status_code})",
                    reason=error_info.get('message', f'HTTP {response.status_code} 错误'),
                    suggestion="请检查 API 配置和网络连接"
                )
        except Exception:
            pass  # 如果解析失败，继续使用其他方法
    
    # 2. 根据HTTP状态码分类（如果没有响应体错误信息）
    if isinstance(exception, requests.exceptions.HTTPError) and hasattr(exception, 'response'):
        response = exception.response
        status_code = response.status_code
        
        if status_code == 401:
            return APIErrorInfo(
                error_type="API Key 验证失败",
                reason="API Key 无效或已过期",
                suggestion="请检查 API Key 是否正确，或前往 API 提供商处生成新的 Key"
            )
        elif status_code == 403:
            return APIErrorInfo(
                error_type="API Key 权限不足",
                reason="当前 API Key 没有访问权限",
                suggestion="请检查 API Key 是否有访问该 API 的权限"
            )
        elif status_code == 404:
            if api_type == 'ai':
                return APIErrorInfo(
                    error_type="API 端点不存在",
                    reason=f"无法找到 API 端点 '{api_base_url}/v1/chat/completions'",
                    suggestion="请检查 API 基础 URL 是否正确"
                )
            else:
                return APIErrorInfo(
                    error_type="API 端点不存在",
                    reason=f"无法找到 API 端点 '{api_base_url}/v1/embeddings'",
                    suggestion="请检查 API 基础 URL 是否正确"
                )
        elif status_code == 400:
            # 尝试解析响应体中的详细错误信息
            error_detail = ""
            try:
                if response.text:
                    error_data = parse_api_error_response(response.text)
                    if error_data and error_data.get('message'):
                        error_detail = error_data.get('message')
            except Exception:
                pass
            
            if error_detail:
                error_lower = error_detail.lower()
                
                # 优先检查模型相关错误（即使错误信息提到 API Key，也可能是模型权限问题）
                model_keywords = ['model', 'model_name', 'model not found', 'model does not exist', 
                                 'model unavailable', 'model not available', 'invalid model',
                                 'unsupported model', 'model not supported']
                
                # 检查是否是模型相关错误
                is_model_error = any(keyword in error_lower for keyword in model_keywords)
                
                # 如果错误信息提到 API Key，但可能是模型权限问题
                # 例如："API key expired" 但换模型能成功，说明可能是该模型需要特定权限
                api_key_keywords = ['api key', 'apikey', 'key expired', 'key invalid', 
                                   'authentication', 'authorization', 'unauthorized']
                has_api_key_mention = any(keyword in error_lower for keyword in api_key_keywords)
                
                if is_model_error:
                    # 明确的模型错误
                    return APIErrorInfo(
                        error_type="模型配置错误",
                        reason=f"模型 '{model_name}' 配置错误：{error_detail}",
                        suggestion="请检查模型名称是否正确，或查看 API 文档确认可用模型列表。如果其他模型可以正常使用，可能是该模型需要特殊权限或配置。"
                    )
                elif has_api_key_mention and not is_model_error:
                    # 提到 API Key，但可能是模型权限问题
                    # 注意：这里不能100%确定，但可以提示用户可能是模型权限问题
                    return APIErrorInfo(
                        error_type="API Key 或模型权限问题",
                        reason=f"API 返回错误：{error_detail}",
                        suggestion="如果其他模型可以正常使用，可能是该模型 '{model_name}' 需要特殊权限或该 API Key 无权访问此模型。请检查：1) 模型名称是否正确 2) API Key 是否有访问该模型的权限 3) 或尝试使用其他模型"
                    )
                elif 'invalid' in error_lower and 'key' in error_lower:
                    return APIErrorInfo(
                        error_type="API Key 格式错误",
                        reason=f"API Key 格式错误：{error_detail}",
                        suggestion="请检查 API Key 格式是否正确"
                    )
                else:
                    # 其他 400 错误
                    return APIErrorInfo(
                        error_type="请求参数错误",
                        reason=f"API 请求参数错误：{error_detail}",
                        suggestion="请检查模型名称、温度参数等配置是否正确。如果其他模型可以正常使用，建议尝试更换模型。"
                    )
            else:
                return APIErrorInfo(
                    error_type="请求参数错误",
                    reason="API 请求参数不正确 (HTTP 400)",
                    suggestion="请检查模型名称、温度参数等配置是否正确，或查看 API 文档。如果其他模型可以正常使用，建议尝试更换模型。"
                )
        elif status_code == 429:
            return APIErrorInfo(
                error_type="API 调用频率过高",
                reason="已达到 API 调用频率限制",
                suggestion="请稍后重试，或检查 API 配额和限流设置"
            )
        elif status_code in [500, 502, 503, 504]:
            return APIErrorInfo(
                error_type="服务器错误",
                reason=f"API 服务器返回错误 (HTTP {status_code})",
                suggestion="服务器暂时不可用，请稍后重试"
            )
    
    # 3. 根据异常类型分类
    error_str = str(exception).lower()
    
    # 超时错误
    if isinstance(exception, requests.exceptions.Timeout):
        return APIErrorInfo(
            error_type="连接超时",
            reason="连接超时（30秒），无法连接到 API 服务器",
            suggestion="请检查网络连接、API 服务器状态，或增加超时时间"
        )
    
    # 连接错误
    if isinstance(exception, requests.exceptions.ConnectionError):
        if '10061' in str(exception) or '拒绝' in str(exception) or 'refused' in error_str:
            return APIErrorInfo(
                error_type="连接被拒绝",
                reason=f"无法连接到服务器 '{api_base_url}'，连接被拒绝",
                suggestion="请检查 API 基础 URL 是否正确，或服务器是否正在运行"
            )
        elif 'dns' in error_str or 'name resolution' in error_str or '无法解析' in str(exception):
            return APIErrorInfo(
                error_type="DNS 解析失败",
                reason=f"无法解析域名 '{api_base_url}'",
                suggestion="请检查 API 基础 URL 中的域名是否正确"
            )
        else:
            return APIErrorInfo(
                error_type="网络连接失败",
                reason=f"无法连接到服务器 '{api_base_url}'",
                suggestion="请检查网络连接、API 基础 URL 是否正确，或服务器是否可访问"
            )
    
    # SSL证书错误
    if isinstance(exception, requests.exceptions.SSLError):
        return APIErrorInfo(
            error_type="SSL 证书验证失败",
            reason="SSL 证书验证失败",
            suggestion="请检查服务器 SSL 证书是否有效，或联系 API 提供商"
        )
    
    # URL格式错误（在发送请求前检查）
    if api_base_url:
        if not api_base_url.startswith(('http://', 'https://')):
            return APIErrorInfo(
                error_type="URL 格式错误",
                reason=f"API 基础 URL 格式不正确：'{api_base_url}'",
                suggestion="URL 应以 'http://' 或 'https://' 开头"
            )
    
    # API Key为空
    if not api_key or api_key.strip() == '':
        return APIErrorInfo(
            error_type="API Key 未填写",
            reason="API Key 为空",
            suggestion="请填写有效的 API Key"
        )
    
    # 模型名称为空
    if not model_name or model_name.strip() == '':
        return APIErrorInfo(
            error_type="模型名称未填写",
            reason="模型名称为空",
            suggestion="请填写有效的模型名称"
        )
    
    # 检查错误消息中的关键词
    if 'invalid api key' in error_str or 'invalid_api_key' in error_str:
        return APIErrorInfo(
            error_type="API Key 无效",
            reason="API Key 格式或内容无效",
            suggestion="请检查 API Key 是否正确，或生成新的 API Key"
        )
    
    if 'model' in error_str and ('not found' in error_str or '不存在' in str(exception)):
        return APIErrorInfo(
            error_type="模型不存在",
            reason=f"模型 '{model_name}' 不存在",
            suggestion="请检查模型名称是否正确，或查看 API 文档确认可用模型"
        )
    
    if 'rate limit' in error_str or '429' in str(exception):
        return APIErrorInfo(
            error_type="API 调用频率过高",
            reason="已达到 API 调用频率限制",
            suggestion="请稍后重试，或检查 API 配额设置"
        )
    
    # 默认错误信息
    error_message = str(exception)
    if len(error_message) > 200:
        error_message = error_message[:200] + "..."
    
    return APIErrorInfo(
        error_type=f"{api_name} 调用失败",
        reason=error_message,
        suggestion="请检查 API 配置、网络连接和服务器状态"
    )


def format_error_for_display(error_info: APIErrorInfo) -> str:
    """
    格式化错误信息用于前端显示
    
    Args:
        error_info: APIErrorInfo对象
        
    Returns:
        格式化后的错误消息字符串
    """
    return error_info.to_message()

