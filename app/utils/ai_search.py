#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 搜索服务工具类"""

import json
import requests
import re
from typing import Any, Dict, List, Optional, Tuple
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


class AIServiceError(Exception):
    """Base error for AI service failures."""


class AICompatibilityError(AIServiceError):
    """Raised when the provider response shape is incompatible."""


class AIEmptyResponseError(AICompatibilityError):
    """Raised when the provider returns no usable text."""


class AIJSONParseError(AICompatibilityError):
    """Raised when a structured response cannot be parsed as JSON."""


AI_TASK_MODEL_FIELDS = {
    "intent": "ai_selected_intent_model",
    "rerank": "ai_selected_rerank_model",
    "translate": "ai_selected_translate_model",
    "site_info": "ai_selected_site_info_model",
}


class AISearchService:
    """AI 搜索服务类"""
    
    def __init__(self, api_base_url: str, api_key: str, model_name: str,
                 temperature: float = 0.7, max_tokens: int = 500):
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = float(temperature) if temperature is not None else 0.7
        self.max_tokens = max_tokens if max_tokens is not None else 500
        self._json_object_response_format_supported: Optional[bool] = None

    def _call_api(self, messages: List[dict], temperature: float = None,
                  max_tokens: int = None, expect_json: bool = False) -> dict:
        data = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens
        }

        allow_json_mode_fallback = False
        if expect_json and self._json_object_response_format_supported is not False:
            data["response_format"] = {"type": "json_object"}
            allow_json_mode_fallback = True

        return self._post_chat_completion(
            data,
            allow_json_mode_fallback=allow_json_mode_fallback
        )

    def _post_chat_completion(self, data: Dict[str, Any],
                              allow_json_mode_fallback: bool = False) -> dict:
        url = f"{self.api_base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if data.get("response_format", {}).get("type") == "json_object":
                self._json_object_response_format_supported = True
            return payload
        except requests.exceptions.HTTPError as e:
            response = e.response
            if allow_json_mode_fallback and self._should_retry_without_json_mode(response):
                self._json_object_response_format_supported = False
                current_app.logger.warning(
                    "AI provider rejected response_format=json_object; retrying without it"
                )
                fallback_data = data.copy()
                fallback_data.pop("response_format", None)
                return self._post_chat_completion(
                    fallback_data,
                    allow_json_mode_fallback=False
                )
            raise Exception(f"AI API 调用失败: {str(e)}")
        except requests.exceptions.Timeout:
            raise Exception("AI API 调用超时，请稍后重试")
        except requests.exceptions.RequestException as e:
            raise Exception(f"AI API 调用失败: {str(e)}")
        except ValueError as e:
            raise AICompatibilityError(f"AI API 返回的不是有效 JSON：{str(e)}")
        except AIServiceError:
            raise
        except Exception as e:
            raise Exception(f"AI API 处理错误: {str(e)}")

    def _should_retry_without_json_mode(
        self,
        response: Optional[requests.Response]
    ) -> bool:
        if response is None or response.status_code not in (400, 404, 415, 422):
            return False

        error_text = (response.text or "").lower()
        unsupported_markers = [
            "response_format",
            "json_object",
            "json schema",
            "json_schema",
            "unsupported",
            "not support",
            "unknown field",
            "invalid parameter",
            "unrecognized request argument",
            "extra inputs are not permitted"
        ]
        return any(marker in error_text for marker in unsupported_markers)

    def _extract_response_text(self, response: Dict[str, Any]) -> str:
        if not isinstance(response, dict):
            raise AICompatibilityError("AI API 返回格式异常，响应不是 JSON 对象")

        issues: List[str] = []
        choices = response.get("choices")
        if isinstance(choices, list):
            for index, choice in enumerate(choices):
                text, issue = self._extract_choice_text(choice, index)
                if text:
                    return text
                if issue:
                    issues.append(issue)

        output_text = self._coerce_content_to_text(response.get("output_text"))
        if output_text:
            return output_text

        output_text = self._coerce_content_to_text(response.get("output"))
        if output_text:
            return output_text

        if issues:
            raise AIEmptyResponseError("；".join(issues[:3]))

        raise AIEmptyResponseError("AI API 返回成功，但没有可用的文本内容")

    def _extract_choice_text(self, choice: Any, index: int) -> Tuple[str, Optional[str]]:
        label = f"choices[{index}]"
        if not isinstance(choice, dict):
            return "", f"{label} 格式异常"

        message = choice.get("message")
        if isinstance(message, dict):
            return self._extract_message_text(message, f"{label}.message")

        text = self._coerce_content_to_text(choice.get("text"))
        if text:
            return text, None

        return "", f"{label} 中没有找到可用的文本字段"

    def _extract_message_text(
        self,
        message: Dict[str, Any],
        label: str
    ) -> Tuple[str, Optional[str]]:
        text = self._coerce_content_to_text(message.get("content"))
        if text:
            return text, None

        text = self._coerce_content_to_text(message.get("text"))
        if text:
            return text, None

        if message.get("tool_calls"):
            return "", f"{label} 返回了 tool_calls，当前功能需要直接文本输出"

        refusal = self._coerce_content_to_text(message.get("refusal"))
        if refusal:
            return "", f"{label} 返回了拒绝信息：{self._truncate_text(refusal, 120)}"

        if message.get("content") is None:
            return "", f"{label}.content 为空"

        return "", f"{label} 中没有可解析的文本内容"

    def _coerce_content_to_text(self, content: Any) -> str:
        if content is None:
            return ""

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, (int, float, bool)):
            return str(content)

        if isinstance(content, list):
            parts = []
            for item in content:
                part = self._coerce_content_to_text(item)
                if part:
                    parts.append(part)
            return "\n".join(parts).strip()

        if isinstance(content, dict):
            parts = []
            for key in ("text", "value", "content", "output_text"):
                if key in content:
                    part = self._coerce_content_to_text(content.get(key))
                    if part:
                        parts.append(part)
            return "\n".join(parts).strip()

        return ""

    def _parse_json_response(self, content: Any) -> Any:
        text = self._coerce_content_to_text(content)
        if not text:
            raise AIEmptyResponseError("AI 返回了空内容，无法解析 JSON")

        for candidate in self._iter_json_candidates(text):
            try:
                parsed = json.loads(candidate)
                return self._decode_nested_json(parsed)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

        raise AIJSONParseError(
            f"无法解析 AI 响应中的 JSON：{self._truncate_text(text, 200)}"
        )

    def _iter_json_candidates(self, text: str) -> List[str]:
        candidates: List[str] = []

        stripped = text.strip()
        if stripped:
            candidates.append(stripped)

        for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE):
            block = block.strip()
            if block:
                candidates.append(block)

        candidates.extend(self._extract_balanced_json_segments(text, "{", "}"))
        candidates.extend(self._extract_balanced_json_segments(text, "[", "]"))

        deduped: List[str] = []
        seen = set()
        for candidate in candidates:
            if candidate and candidate not in seen:
                deduped.append(candidate)
                seen.add(candidate)
        return deduped

    def _extract_balanced_json_segments(
        self,
        text: str,
        opener: str,
        closer: str
    ) -> List[str]:
        segments = []
        depth = 0
        start = None
        in_string = False
        escaped = False

        for index, char in enumerate(text):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue

            if char == opener:
                if depth == 0:
                    start = index
                depth += 1
            elif char == closer and depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    segment = text[start:index + 1].strip()
                    if segment:
                        segments.append(segment)
                    start = None

        return segments

    def _decode_nested_json(self, value: Any) -> Any:
        current = value
        for _ in range(2):
            if not isinstance(current, str):
                break
            stripped = current.strip()
            if not stripped:
                break
            if stripped[0] not in ('{', '[', '"'):
                break
            try:
                current = json.loads(stripped)
            except (json.JSONDecodeError, TypeError, ValueError):
                break
        return current

    def _normalize_string_list(self, value: Any) -> List[str]:
        if isinstance(value, list):
            raw_items = value
        elif value is None:
            raw_items = []
        else:
            raw_items = re.split(r"[\n,，、]+", self._coerce_content_to_text(value))

        result = []
        seen = set()
        for item in raw_items:
            text = self._coerce_content_to_text(item).strip()
            if text and text not in seen:
                result.append(text)
                seen.add(text)
        return result

    def _normalize_intent_result(self, payload: Any, user_query: str) -> dict:
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
            payload = payload[0]

        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            payload = payload["result"]

        if not isinstance(payload, dict):
            raise AIJSONParseError("AI 意图分析返回的不是 JSON 对象")

        keywords = self._normalize_string_list(payload.get("keywords"))
        if not keywords and user_query:
            keywords = [user_query]

        search_type = self._coerce_content_to_text(payload.get("search_type")).lower()
        if search_type not in {"exact", "fuzzy", "semantic"}:
            search_type = "semantic" if len(keywords) > 1 else "fuzzy"

        return {
            "intent": self._coerce_content_to_text(payload.get("intent"))
            or f"用户想要查找与“{user_query}”相关的网站",
            "keywords": keywords,
            "related_terms": self._normalize_string_list(payload.get("related_terms")),
            "category_hints": self._normalize_string_list(payload.get("category_hints")),
            "search_type": search_type
        }

    def _normalize_recommendation_item(self, item: Any) -> Optional[dict]:
        if not isinstance(item, dict):
            return None

        website_id = item.get("website_id", item.get("id", item.get("websiteId")))
        if isinstance(website_id, str):
            website_id = website_id.strip()
            if website_id.isdigit():
                website_id = int(website_id)
            else:
                match = re.search(r"\d+", website_id)
                website_id = int(match.group()) if match else None

        if not isinstance(website_id, int):
            return None

        score = item.get(
            "relevance_score",
            item.get("score", item.get("relevance", item.get("confidence")))
        )
        try:
            score = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0

        return {
            "website_id": website_id,
            "relevance_score": score,
            "reason": self._coerce_content_to_text(
                item.get("reason", item.get("explanation", item.get("summary")))
            )
        }

    def _normalize_recommendations_result(self, payload: Any) -> dict:
        summary = ""
        raw_recommendations = None

        if isinstance(payload, list):
            raw_recommendations = payload
        elif isinstance(payload, dict):
            summary = self._coerce_content_to_text(
                payload.get("summary", payload.get("message"))
            )
            for key in ("recommendations", "results", "items"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    raw_recommendations = candidate
                    break

            if raw_recommendations is None:
                nested = payload.get("result")
                if isinstance(nested, dict):
                    summary = summary or self._coerce_content_to_text(
                        nested.get("summary", nested.get("message"))
                    )
                    for key in ("recommendations", "results", "items"):
                        candidate = nested.get(key)
                        if isinstance(candidate, list):
                            raw_recommendations = candidate
                            break
                elif isinstance(nested, list):
                    raw_recommendations = nested

            if raw_recommendations is None and any(
                key in payload for key in ("website_id", "id", "websiteId")
            ):
                raw_recommendations = [payload]
        else:
            raise AIJSONParseError("AI 推荐结果不是可解析的 JSON")

        normalized = []
        for item in raw_recommendations or []:
            recommendation = self._normalize_recommendation_item(item)
            if recommendation:
                normalized.append(recommendation)

        if raw_recommendations and not normalized:
            raise AIJSONParseError("AI 返回了推荐数据，但没有可用的 website_id")

        return {
            "recommendations": normalized,
            "summary": summary
        }

    def _normalize_website_info_result(self, payload: Any) -> dict:
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
            payload = payload[0]

        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]

        if not isinstance(payload, dict):
            raise AIJSONParseError("AI 网站信息返回的不是 JSON 对象")

        title = self._coerce_content_to_text(payload.get("title", payload.get("name")))
        description = self._coerce_content_to_text(
            payload.get("description", payload.get("desc", payload.get("summary")))
        )

        if not title and not description:
            raise AIJSONParseError("AI 网站信息返回中缺少 title 或 description")

        return {
            "title": title,
            "description": description
        }

    def _cleanup_plain_text(self, text: str) -> str:
        text = text.strip()
        fenced_match = re.fullmatch(r"```(?:[\w+-]+)?\s*([\s\S]*?)```", text)
        if fenced_match:
            text = fenced_match.group(1).strip()
        return text

    def _truncate_text(self, text: str, limit: int = 120) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    def analyze_search_intent(self, user_query: str) -> dict:
        prompt = AI_SEARCH_PROMPT_TEMPLATE.format(user_query=user_query)
        messages = [
            {
                "role": "system",
                "content": "你是专业的网站搜索助手，擅长理解用户搜索意图并提取关键词。"
            },
            {"role": "user", "content": prompt}
        ]

        try:
            response = self._call_api(messages, max_tokens=300, expect_json=True)
            content = self._extract_response_text(response)
            intent_result = self._parse_json_response(content)
            return self._normalize_intent_result(intent_result, user_query)
        except Exception as e:
            current_app.logger.error(f"AI 意图分析失败: {str(e)}")
            raise

    def recommend_websites(self, user_query: str, intent: dict, websites: List[dict],
                          vector_scores: Optional[Dict[int, float]] = None,
                          max_recommendations: int = 20) -> dict:
        websites_with_scores = []
        for website in websites:
            website_dict = website.copy() if isinstance(website, dict) else {
                "id": website.id if hasattr(website, "id") else website.get("id"),
                "title": website.title if hasattr(website, "title") else website.get("title", ""),
                "description": website.description if hasattr(website, "description") else website.get("description", ""),
                "category": website.category.name if hasattr(website, "category") and website.category else website.get("category", ""),
                "url": website.url if hasattr(website, "url") else website.get("url", "")
            }
            if vector_scores and website_dict.get("id") in vector_scores:
                website_dict["vector_score"] = vector_scores[website_dict["id"]]
            websites_with_scores.append(website_dict)

        if vector_scores:
            websites_with_vector = [
                website for website in websites_with_scores
                if website.get("vector_score") is not None
            ]
            websites_without_vector = [
                website for website in websites_with_scores
                if website.get("vector_score") is None
            ]
            websites_with_vector.sort(
                key=lambda item: item.get("vector_score", 0), reverse=True
            )
            websites_for_ai = websites_with_vector[:150] + websites_without_vector[:50]
        else:
            websites_with_desc = [
                website for website in websites_with_scores if website.get("description")
            ]
            websites_without_desc = [
                website for website in websites_with_scores if not website.get("description")
            ]
            websites_for_ai = (websites_with_desc[:150] + websites_without_desc[:50])[:200]

        websites_for_ai = websites_for_ai[:200]

        prompt = AI_SEARCH_RECOMMEND_PROMPT_TEMPLATE.format(
            user_query=user_query,
            intent=intent.get("intent", ""),
            total_count=len(websites),
            websites_list=json.dumps(websites_for_ai, ensure_ascii=False, indent=2),
            max_recommendations=max_recommendations
        )

        messages = [
            {
                "role": "system",
                "content": "你是专业的网站推荐助手，能够准确评估网站与用户查询的相关性。"
            },
            {"role": "user", "content": prompt}
        ]

        try:
            response = self._call_api(messages, max_tokens=2000, expect_json=True)
            content = self._extract_response_text(response)
            parsed = self._parse_json_response(content)
            return self._normalize_recommendations_result(parsed)
        except Exception as e:
            current_app.logger.error(f"AI 推荐失败: {str(e)}")
            raise

    def translate_text(self, text: str, target_lang: str = 'zh') -> str:
        if not text or not text.strip():
            return text

        lang_name = "中文" if target_lang == 'zh' else target_lang
        prompt = f"""请将以下文本翻译成{lang_name}，要求：
1. 保持原意准确
2. 语言自然流畅
3. 只返回翻译结果，不要添加任何解释或说明

原文：
{text}

翻译结果："""

        messages = [
            {
                "role": "system",
                "content": "你是专业的翻译助手，擅长将各种语言准确翻译成目标语言。"
            },
            {"role": "user", "content": prompt}
        ]

        try:
            response = self._call_api(messages, temperature=0.3, max_tokens=500)
            translated = self._extract_response_text(response)
            translated = self._cleanup_plain_text(translated).strip('"').strip("'").strip()
            if not translated:
                raise AIEmptyResponseError("AI 返回了空的翻译结果")
            return translated
        except Exception as e:
            current_app.logger.error(f"AI 翻译失败: {str(e)}")
            raise Exception(f"翻译失败: {str(e)}")

    def generate_website_info(self, url: str) -> dict:
        prompt = f"""请根据以下网站 URL，生成一个简洁准确的网站标题和描述。

网站 URL：{url}

要求：
1. 标题：简洁明了，不超过 20 个字符，准确反映网站的主要功能或内容
2. 描述：准确描述网站的主要功能、用途和特点，不超过 200 个字符
3. 使用中文
4. 如果无法确定网站内容，可以根据 URL 和域名进行合理推测

请以 JSON 格式返回：
{{
  "title": "网站标题",
  "description": "网站描述"
}}

只返回 JSON，不要输出其他内容。"""

        messages = [
            {
                "role": "system",
                "content": "你是专业的网站分析助手，能够根据 URL 推断网站功能和内容。"
            },
            {"role": "user", "content": prompt}
        ]

        try:
            response = self._call_api(
                messages, temperature=0.5, max_tokens=300, expect_json=True
            )
            content = self._extract_response_text(response)
            parsed = self._parse_json_response(content)
            return self._normalize_website_info_result(parsed)
        except Exception as e:
            current_app.logger.error(f"AI 生成网站信息失败: {str(e)}")
            raise Exception(f"生成网站信息失败: {str(e)}")


def get_ai_model_for_task(settings, task: Optional[str] = None) -> Optional[str]:
    auto_enabled = bool(getattr(settings, "ai_auto_model_selection_enabled", False))
    manual_model = (getattr(settings, "ai_model_name", None) or "").strip()

    if task and auto_enabled:
        field_name = AI_TASK_MODEL_FIELDS.get(task)
        if field_name:
            selected_model = (getattr(settings, field_name, None) or "").strip()
            if selected_model:
                return selected_model

        fallback_model = (getattr(settings, "ai_selected_fallback_model", None) or "").strip()
        if fallback_model:
            return fallback_model

    if manual_model:
        return manual_model

    if auto_enabled:
        for field_name in AI_TASK_MODEL_FIELDS.values():
            selected_model = (getattr(settings, field_name, None) or "").strip()
            if selected_model:
                return selected_model

        fallback_model = (getattr(settings, "ai_selected_fallback_model", None) or "").strip()
        if fallback_model:
            return fallback_model

    return None


def get_ai_temperature_for_task(settings, task: Optional[str] = None) -> float:
    try:
        temperature = float(getattr(settings, "ai_temperature", 0.7) or 0.7)
    except (TypeError, ValueError):
        temperature = 0.7

    # 结构化任务更适合低温度输出，减少多余文本与格式漂移。
    if task in {"intent", "rerank"}:
        return min(temperature, 0.2)

    return temperature


def create_ai_service_from_settings(
    settings,
    require_enabled: bool = False,
    task: Optional[str] = None
) -> Optional[AISearchService]:
    """
    从站点设置创建 AI 搜索服务
    
    Args:
        settings: SiteSettings 对象
        
    Returns:
        AISearchService 实例，如果配置不完整则返回 None
    """
    if require_enabled and not settings.ai_search_enabled:
        return None
    
    model_name = get_ai_model_for_task(settings, task=task)

    if not all([settings.ai_api_base_url, settings.ai_api_key, model_name]):
        return None
    
    try:
        return AISearchService(
            api_base_url=settings.ai_api_base_url,
            api_key=settings.ai_api_key,
            model_name=model_name,
            temperature=get_ai_temperature_for_task(settings, task=task),
            max_tokens=settings.ai_max_tokens
        )
    except Exception as e:
        current_app.logger.error(f"创建 AI 服务失败: {str(e)}")
        return None

