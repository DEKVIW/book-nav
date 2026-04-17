#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI search and content utility services."""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import current_app, has_app_context


AI_SEARCH_PROMPT_TEMPLATE = """You are a website search assistant.
Analyze the user's intent and return JSON only.

User query: {user_query}

Return exactly this JSON shape:
{{
  "intent": "user intent",
  "keywords": ["keyword1", "keyword2"],
  "related_terms": ["term1", "term2"],
  "category_hints": ["category1", "category2"],
  "search_type": "exact|fuzzy|semantic"
}}"""

AI_SEARCH_RECOMMEND_PROMPT_TEMPLATE = """You are a website ranking assistant.
Rank candidate websites for the given user query and return JSON only.

User query: {user_query}
Intent summary: {intent}
Candidate count: {total_count}
Maximum recommendations: {max_recommendations}
Candidates:
{websites_list}

Return exactly this JSON shape:
{{
  "recommendations": [
    {{
      "website_id": 1,
      "relevance_score": 0.95,
      "reason": "why it matches"
    }}
  ],
  "summary": "short summary"
}}"""

WEBSITE_INFO_PROMPT_TEMPLATE = """Generate website metadata from the URL below.
Return JSON only.

Website URL: {url}

Requirements:
1. title: concise and descriptive, ideally within 20 characters.
2. description: accurate summary of the website's function or content, ideally within 200 characters.
3. If you are unsure, infer conservatively from the domain and URL path.

Return exactly:
{{
  "title": "website title",
  "description": "website description"
}}"""


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
AI_TASK_KEYS = tuple(AI_TASK_MODEL_FIELDS.keys())

AI_INTERFACE_MODE_AUTO = "auto"
AI_INTERFACE_MODE_CHAT = "chat"
AI_INTERFACE_MODE_RESPONSES = "responses"
AI_INTERFACE_MODE_VALUES = {
    AI_INTERFACE_MODE_AUTO,
    AI_INTERFACE_MODE_CHAT,
    AI_INTERFACE_MODE_RESPONSES,
}

AI_REQUEST_TIMEOUT = (15, 60)
AI_STREAM_REQUEST_TIMEOUT = (15, 90)


def _get_logger():
    if has_app_context():
        return current_app.logger
    return logging.getLogger(__name__)


class AIFailoverService:
    """Try multiple configured AI services in order until one succeeds."""

    def __init__(self, services: List["AISearchService"]):
        self._services = services
        self.last_protocol_used: str = ""
        self.last_attempt_trace: List[Dict[str, str]] = []
        self.provider_id = None
        self.provider_name = ""
        self.model_name = ""
        self.interface_mode = ""
        self.candidate_descriptions = [
            {
                "provider_id": getattr(service, "provider_id", None),
                "provider_name": getattr(service, "provider_name", ""),
                "model_name": getattr(service, "model_name", ""),
                "interface_mode": getattr(service, "interface_mode", ""),
            }
            for service in services
        ]

    def _sync_runtime_metadata(self, service: "AISearchService") -> None:
        self.last_protocol_used = getattr(service, "last_protocol_used", "") or ""
        self.last_attempt_trace = list(getattr(service, "last_attempt_trace", []) or [])
        self.provider_id = getattr(service, "provider_id", None)
        self.provider_name = getattr(service, "provider_name", "") or ""
        self.model_name = getattr(service, "model_name", "") or ""
        self.interface_mode = getattr(service, "interface_mode", "") or ""

    def _run(self, method_name: str, *args, **kwargs):
        errors: List[str] = []
        last_error: Optional[Exception] = None

        for service in self._services:
            try:
                result = getattr(service, method_name)(*args, **kwargs)
                self._sync_runtime_metadata(service)
                return result
            except Exception as exc:
                last_error = exc
                provider_name = getattr(service, "provider_name", "") or "AI"
                model_name = getattr(service, "model_name", "") or "unknown"
                errors.append(f"{provider_name}/{model_name}: {str(exc)}")
                _get_logger().warning(
                    "AI failover attempt failed for %s via %s/%s: %s",
                    method_name,
                    provider_name,
                    model_name,
                    str(exc),
                )

        if last_error is not None:
            if errors:
                raise Exception("；".join(errors[:3]))
            raise last_error
        raise Exception("没有可用的 AI 服务候选项")

    def probe_text_output(self):
        return self._run("probe_text_output")

    def analyze_search_intent(self, user_query: str):
        return self._run("analyze_search_intent", user_query)

    def recommend_websites(
        self,
        user_query: str,
        intent: dict,
        websites: List[dict],
        vector_scores: Optional[Dict[int, float]] = None,
        max_recommendations: int = 20,
    ):
        return self._run(
            "recommend_websites",
            user_query,
            intent,
            websites,
            vector_scores=vector_scores,
            max_recommendations=max_recommendations,
        )

    def translate_text(self, text: str, target_lang: str = "zh"):
        return self._run("translate_text", text, target_lang=target_lang)

    def generate_website_info(self, url: str):
        return self._run("generate_website_info", url)


class AISearchService:
    """Unified AI service wrapper for text and structured tasks."""

    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 500,
        interface_mode: str = AI_INTERFACE_MODE_AUTO,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = float(temperature) if temperature is not None else 0.7
        self.max_tokens = max_tokens if max_tokens is not None else 500
        self.interface_mode = self._normalize_interface_mode(interface_mode)
        self._json_object_response_format_supported: Optional[bool] = None
        self.last_protocol_used: str = ""
        self.last_attempt_trace: List[Dict[str, str]] = []
        self.provider_id = None
        self.provider_name = ""
        self.service_source = ""

    def _call_api(
        self,
        messages: List[dict],
        temperature: float = None,
        max_tokens: int = None,
        expect_json: bool = False,
    ) -> dict:
        chat_data = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }

        allow_json_mode_fallback = False
        if expect_json and self._json_object_response_format_supported is not False:
            chat_data["response_format"] = {"type": "json_object"}
            allow_json_mode_fallback = True

        self.last_protocol_used = ""
        self.last_attempt_trace = []

        attempts = self._build_request_attempts(
            messages=messages,
            chat_data=chat_data,
            allow_json_mode_fallback=allow_json_mode_fallback,
        )

        last_error: Optional[Exception] = None
        for attempt_name, attempt_func in attempts:
            try:
                payload = attempt_func()
                response_text = self._extract_response_text(payload)
                if not response_text:
                    raise AIEmptyResponseError("AI API 返回了空文本内容")
                if expect_json:
                    self._parse_json_response(response_text)

                self.last_protocol_used = attempt_name
                self.last_attempt_trace.append(
                    {
                        "protocol": attempt_name,
                        "status": "success",
                    }
                )
                return payload
            except Exception as exc:
                last_error = exc
                self.last_attempt_trace.append(
                    {
                        "protocol": attempt_name,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                if self._should_try_next_attempt(exc):
                    _get_logger().warning(
                        "AI request via %s failed for model %s, falling back: %s",
                        attempt_name,
                        self.model_name,
                        str(exc),
                    )
                    continue
                raise self._normalize_api_exception(exc)

        if last_error is not None:
            raise self._normalize_api_exception(last_error)
        raise AIServiceError("AI 请求失败：没有可用的接口模式")

    def _build_request_attempts(
        self,
        messages: List[dict],
        chat_data: Dict[str, Any],
        allow_json_mode_fallback: bool = False,
    ) -> List[Tuple[str, Any]]:
        responses_data = self._build_responses_request(messages, chat_data)
        attempts: List[Tuple[str, Any]] = []

        if self.interface_mode == AI_INTERFACE_MODE_CHAT:
            attempts.extend(
                [
                    (
                        "chat",
                        lambda: self._post_chat_completion(
                            chat_data,
                            allow_json_mode_fallback=allow_json_mode_fallback,
                        ),
                    ),
                    (
                        "chat_stream",
                        lambda: self._stream_chat_completion(chat_data),
                    ),
                ]
            )
        elif self.interface_mode == AI_INTERFACE_MODE_RESPONSES:
            attempts.append(
                (
                    "responses",
                    lambda: self._post_responses(
                        responses_data,
                        fallback_prompt=self._flatten_messages_as_prompt(messages),
                    ),
                )
            )
        else:
            attempts.extend(
                [
                    (
                        "chat",
                        lambda: self._post_chat_completion(
                            chat_data,
                            allow_json_mode_fallback=allow_json_mode_fallback,
                        ),
                    ),
                    (
                        "chat_stream",
                        lambda: self._stream_chat_completion(chat_data),
                    ),
                    (
                        "responses",
                        lambda: self._post_responses(
                            responses_data,
                            fallback_prompt=self._flatten_messages_as_prompt(messages),
                        ),
                    ),
                ]
            )

        return attempts

    def _normalize_interface_mode(self, interface_mode: Optional[str]) -> str:
        value = (interface_mode or "").strip().lower()
        if value not in AI_INTERFACE_MODE_VALUES:
            return AI_INTERFACE_MODE_AUTO
        return value

    def _normalize_api_exception(self, error: Exception) -> Exception:
        if isinstance(error, AIServiceError):
            return error
        if isinstance(error, requests.exceptions.Timeout):
            return Exception("AI API 调用超时，请稍后重试")
        if isinstance(error, requests.exceptions.HTTPError):
            return Exception(f"AI API 调用失败: {str(error)}")
        if isinstance(error, requests.exceptions.RequestException):
            return Exception(f"AI API 调用失败: {str(error)}")
        if isinstance(error, ValueError):
            return AICompatibilityError(f"AI API 返回的不是有效 JSON：{str(error)}")
        return Exception(f"AI API 处理错误: {str(error)}")

    def _should_try_next_attempt(self, error: Exception) -> bool:
        if isinstance(error, (AICompatibilityError, AIEmptyResponseError, AIJSONParseError)):
            return True
        if isinstance(error, ValueError):
            return True
        if isinstance(error, requests.exceptions.HTTPError):
            return self._is_retryable_http_error(error.response)
        return False

    def _response_looks_like_sse(self, response: Optional[requests.Response]) -> bool:
        if response is None:
            return False

        content_type = (response.headers.get("Content-Type") or "").lower()
        if "text/event-stream" in content_type:
            return True

        preview = (response.text or "").lstrip("\ufeff\r\n\t ")
        return preview.startswith("data:") or preview.startswith("event:")

    def _iter_sse_payloads(self, response: requests.Response):
        for raw_line in response.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = raw_line.strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line:
                continue
            if line == "[DONE]":
                break

            try:
                payload = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            if isinstance(payload, dict):
                yield payload

    def _parse_chat_sse_response(self, response: requests.Response) -> dict:
        text_parts: List[str] = []
        has_event_payload = False

        for payload in self._iter_sse_payloads(response):
            has_event_payload = True
            text_parts.extend(self._extract_chat_stream_text_parts(payload))

        aggregated_text = "".join(text_parts).strip()
        if aggregated_text:
            return {
                "choices": [
                    {
                        "message": {
                            "content": aggregated_text,
                        }
                    }
                ]
            }

        if has_event_payload:
            raise AIEmptyResponseError("Chat 流式返回成功，但没有拼接出可用文本")
        raise AICompatibilityError("Chat 流式返回格式异常，未收到可解析的事件数据")

    def _extract_responses_sse_snapshot_text(self, payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""

        for key in ("text", "part", "item", "response", "output_text", "output"):
            if key not in payload:
                continue
            text = self._coerce_content_to_text(payload.get(key))
            if text:
                return text
        return ""

    def _parse_responses_sse_response(self, response: requests.Response) -> dict:
        delta_parts: List[str] = []
        snapshot_text = ""
        final_response: Optional[Dict[str, Any]] = None
        has_event_payload = False

        for payload in self._iter_sse_payloads(response):
            has_event_payload = True
            payload_type = str(payload.get("type") or "")
            if payload_type.endswith(".delta"):
                delta_text = self._coerce_stream_content_to_text(payload.get("delta"))
                if delta_text:
                    delta_parts.append(delta_text)
                    continue

            snapshot_candidate = self._extract_responses_sse_snapshot_text(payload)
            if snapshot_candidate:
                snapshot_text = snapshot_candidate

            response_payload = payload.get("response")
            if isinstance(response_payload, dict) and payload_type.startswith("response."):
                final_response = response_payload

        aggregated_text = "".join(delta_parts).strip() or snapshot_text.strip()
        if final_response:
            normalized_response = dict(final_response)
            if aggregated_text and not self._coerce_content_to_text(normalized_response.get("output_text")):
                normalized_response["output_text"] = aggregated_text
            if aggregated_text and not self._coerce_content_to_text(normalized_response.get("output")):
                normalized_response["output"] = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": aggregated_text,
                            }
                        ],
                    }
                ]
            return normalized_response

        if aggregated_text:
            return {
                "output_text": aggregated_text,
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": aggregated_text,
                            }
                        ],
                    }
                ],
            }

        if has_event_payload:
            raise AIEmptyResponseError("Responses 流式返回成功，但没有拼接出可用文本")
        raise AICompatibilityError("Responses 流式返回格式异常，未收到可解析的事件数据")

    def _is_retryable_http_error(self, response: Optional[requests.Response]) -> bool:
        if response is None:
            return False

        if response.status_code in (400, 404, 405, 415, 422, 501):
            return True

        error_text = (response.text or "").lower()
        retryable_markers = [
            "unsupported",
            "not support",
            "unknown field",
            "invalid parameter",
            "unrecognized request argument",
            "extra inputs are not permitted",
            "chat/completions",
            "responses",
            "response_format",
            "json_object",
            "stream",
            "input_text",
            "max_output_tokens",
            "instructions",
        ]
        return any(marker in error_text for marker in retryable_markers)

    def _post_chat_completion(
        self,
        data: Dict[str, Any],
        allow_json_mode_fallback: bool = False,
        allow_temperature_fallback: bool = True,
    ) -> dict:
        url = f"{self.api_base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request_data = dict(data)
        request_data.setdefault("stream", False)
        response: Optional[requests.Response] = None

        try:
            response = requests.post(url, json=request_data, headers=headers, timeout=AI_REQUEST_TIMEOUT)
            response.raise_for_status()
            if self._response_looks_like_sse(response):
                _get_logger().warning(
                    "AI provider returned SSE for chat/completions with stream disabled; parsing as stream"
                )
                payload = self._parse_chat_sse_response(response)
            else:
                payload = response.json()
            if request_data.get("response_format", {}).get("type") == "json_object":
                self._json_object_response_format_supported = True
            return payload
        except requests.exceptions.HTTPError as exc:
            response = exc.response
            if allow_json_mode_fallback and self._should_retry_without_json_mode(response):
                self._json_object_response_format_supported = False
                _get_logger().warning(
                    "AI provider rejected response_format=json_object; retrying without it"
                )
                fallback_data = dict(request_data)
                fallback_data.pop("response_format", None)
                return self._post_chat_completion(
                    fallback_data,
                    allow_json_mode_fallback=False,
                    allow_temperature_fallback=allow_temperature_fallback,
                )
            if (
                allow_temperature_fallback
                and "temperature" in request_data
                and self._should_retry_without_temperature(response)
            ):
                _get_logger().warning(
                    "AI provider rejected temperature for chat/completions; retrying without it"
                )
                fallback_data = dict(request_data)
                fallback_data.pop("temperature", None)
                return self._post_chat_completion(
                    fallback_data,
                    allow_json_mode_fallback=allow_json_mode_fallback,
                    allow_temperature_fallback=False,
                )
            raise

    def _stream_chat_completion(self, data: Dict[str, Any]) -> dict:
        url = f"{self.api_base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        stream_data = dict(data)
        stream_data.pop("response_format", None)
        stream_data["stream"] = True

        response = requests.post(
            url,
            json=stream_data,
            headers=headers,
            timeout=AI_STREAM_REQUEST_TIMEOUT,
            stream=True,
        )
        response.raise_for_status()
        return self._parse_chat_sse_response(response)

    def _build_responses_request(
        self,
        messages: List[dict],
        chat_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        instructions: List[str] = []
        input_items: List[dict] = []

        for message in messages:
            if not isinstance(message, dict):
                continue

            role = (message.get("role") or "user").strip().lower()
            text = self._coerce_content_to_text(message.get("content")).strip()
            if not text:
                continue

            if role == "system":
                instructions.append(text)
                continue

            input_items.append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": text,
                        }
                    ],
                }
            )

        data: Dict[str, Any] = {
            "model": self.model_name,
            "input": input_items or self._flatten_messages_as_prompt(messages),
            "max_output_tokens": chat_data.get("max_tokens", self.max_tokens),
        }

        temperature = chat_data.get("temperature")
        if temperature is not None:
            data["temperature"] = temperature

        if instructions:
            data["instructions"] = "\n\n".join(instructions)

        return data

    def _post_responses(
        self,
        data: Dict[str, Any],
        fallback_prompt: str = "",
        allow_prompt_fallback: bool = True,
        allow_instructions_fallback: bool = True,
        allow_temperature_fallback: bool = True,
        allow_legacy_max_tokens_fallback: bool = True,
    ) -> dict:
        url = f"{self.api_base_url}/v1/responses"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request_data = dict(data)
        request_data.setdefault("stream", False)

        try:
            response = requests.post(url, json=request_data, headers=headers, timeout=AI_REQUEST_TIMEOUT)
            response.raise_for_status()
            if self._response_looks_like_sse(response):
                _get_logger().warning(
                    "AI provider returned SSE for responses with stream disabled; parsing as stream"
                )
                return self._parse_responses_sse_response(response)
            return response.json()
        except requests.exceptions.HTTPError as exc:
            response = exc.response
            if (
                allow_instructions_fallback
                and "instructions" in request_data
                and self._should_retry_without_instructions(response)
            ):
                fallback_data = dict(request_data)
                fallback_data.pop("instructions", None)
                return self._post_responses(
                    fallback_data,
                    fallback_prompt=fallback_prompt,
                    allow_prompt_fallback=allow_prompt_fallback,
                    allow_instructions_fallback=False,
                    allow_temperature_fallback=allow_temperature_fallback,
                    allow_legacy_max_tokens_fallback=allow_legacy_max_tokens_fallback,
                )
            if (
                allow_temperature_fallback
                and "temperature" in request_data
                and self._should_retry_without_temperature(response)
            ):
                fallback_data = dict(request_data)
                fallback_data.pop("temperature", None)
                return self._post_responses(
                    fallback_data,
                    fallback_prompt=fallback_prompt,
                    allow_prompt_fallback=allow_prompt_fallback,
                    allow_instructions_fallback=allow_instructions_fallback,
                    allow_temperature_fallback=False,
                    allow_legacy_max_tokens_fallback=allow_legacy_max_tokens_fallback,
                )
            if (
                allow_legacy_max_tokens_fallback
                and "max_output_tokens" in request_data
                and self._should_retry_responses_with_legacy_max_tokens(response)
            ):
                fallback_data = dict(request_data)
                fallback_data["max_tokens"] = fallback_data.pop("max_output_tokens")
                return self._post_responses(
                    fallback_data,
                    fallback_prompt=fallback_prompt,
                    allow_prompt_fallback=allow_prompt_fallback,
                    allow_instructions_fallback=allow_instructions_fallback,
                    allow_temperature_fallback=allow_temperature_fallback,
                    allow_legacy_max_tokens_fallback=False,
                )
            if (
                allow_prompt_fallback
                and fallback_prompt
                and self._should_retry_responses_with_flat_prompt(response)
            ):
                fallback_data = dict(request_data)
                fallback_data["input"] = fallback_prompt
                return self._post_responses(
                    fallback_data,
                    fallback_prompt="",
                    allow_prompt_fallback=False,
                    allow_instructions_fallback=allow_instructions_fallback,
                    allow_temperature_fallback=allow_temperature_fallback,
                    allow_legacy_max_tokens_fallback=allow_legacy_max_tokens_fallback,
                )
            raise

    def _extract_chat_stream_text_parts(self, payload: Dict[str, Any]) -> List[str]:
        parts: List[str] = []

        choices = payload.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if delta is not None:
                    delta_text = self._coerce_stream_content_to_text(delta)
                    if delta_text:
                        parts.append(delta_text)
                message = choice.get("message")
                if message is not None:
                    message_text = self._coerce_stream_content_to_text(message)
                    if message_text:
                        parts.append(message_text)
                text = self._coerce_stream_content_to_text(choice.get("text"))
                if text:
                    parts.append(text)

        output_text = self._coerce_stream_content_to_text(payload.get("output_text"))
        if output_text:
            parts.append(output_text)

        return parts

    def _coerce_stream_content_to_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, (int, float, bool)):
            return str(content)
        if isinstance(content, list):
            return "".join(
                part
                for part in (self._coerce_stream_content_to_text(item) for item in content)
                if part
            )
        if isinstance(content, dict):
            for key in ("text", "content", "delta", "output_text", "value"):
                if key in content:
                    text = self._coerce_stream_content_to_text(content.get(key))
                    if text:
                        return text
            return ""
        return ""

    def _flatten_messages_as_prompt(self, messages: List[dict]) -> str:
        segments: List[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = (message.get("role") or "user").strip().lower()
            text = self._coerce_content_to_text(message.get("content")).strip()
            if not text:
                continue
            role_label = {
                "system": "System",
                "assistant": "Assistant",
                "user": "User",
            }.get(role, "User")
            segments.append(f"{role_label}: {text}")
        return "\n\n".join(segments)

    def _should_retry_without_json_mode(self, response: Optional[requests.Response]) -> bool:
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
            "extra inputs are not permitted",
        ]
        return any(marker in error_text for marker in unsupported_markers)

    def _should_retry_without_temperature(self, response: Optional[requests.Response]) -> bool:
        if response is None or response.status_code not in (400, 404, 415, 422):
            return False

        error_text = (response.text or "").lower()
        generic_markers = [
            "unsupported",
            "not support",
            "unknown field",
            "invalid parameter",
            "unrecognized request argument",
            "extra inputs are not permitted",
        ]
        return "temperature" in error_text and any(marker in error_text for marker in generic_markers)

    def _should_retry_without_instructions(self, response: Optional[requests.Response]) -> bool:
        if response is None or response.status_code not in (400, 404, 415, 422):
            return False

        error_text = (response.text or "").lower()
        generic_markers = [
            "unsupported",
            "not support",
            "unknown field",
            "invalid parameter",
            "unrecognized request argument",
            "extra inputs are not permitted",
        ]
        return "instructions" in error_text and any(marker in error_text for marker in generic_markers)

    def _should_retry_responses_with_flat_prompt(self, response: Optional[requests.Response]) -> bool:
        if response is None or response.status_code not in (400, 404, 415, 422):
            return False

        error_text = (response.text or "").lower()
        unsupported_markers = [
            "input_text",
            "expected a string",
            "invalid type",
            "messages",
            "unsupported",
            "not support",
            "extra inputs are not permitted",
        ]
        return any(marker in error_text for marker in unsupported_markers)

    def _should_retry_responses_with_legacy_max_tokens(self, response: Optional[requests.Response]) -> bool:
        if response is None or response.status_code not in (400, 404, 415, 422):
            return False

        error_text = (response.text or "").lower()
        generic_markers = [
            "unsupported",
            "not support",
            "unknown field",
            "invalid parameter",
            "unrecognized request argument",
        ]
        return "max_output_tokens" in error_text and any(
            marker in error_text for marker in generic_markers
        )

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

        output = response.get("output")
        output_text = self._coerce_content_to_text(output)
        if output_text:
            return output_text

        content_text = self._coerce_content_to_text(response.get("content"))
        if content_text:
            return content_text

        candidates_text = self._extract_candidates_text(response.get("candidates"))
        if candidates_text:
            return candidates_text

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
        label: str,
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
            parts: List[str] = []
            for item in content:
                part = self._coerce_content_to_text(item)
                if part:
                    parts.append(part)
            return "\n".join(parts).strip()

        if isinstance(content, dict):
            if content.get("type") == "text" and isinstance(content.get("text"), str):
                return content["text"].strip()
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"].strip()

            parts: List[str] = []
            for key in (
                "text",
                "value",
                "content",
                "output_text",
                "parts",
                "message",
                "output",
                "delta",
            ):
                if key in content:
                    part = self._coerce_content_to_text(content.get(key))
                    if part:
                        parts.append(part)
            return "\n".join(parts).strip()

        return ""

    def _extract_candidates_text(self, candidates: Any) -> str:
        if not isinstance(candidates, list):
            return ""

        parts: List[str] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            part = self._coerce_content_to_text(candidate.get("content"))
            if part:
                parts.append(part)

        return "\n".join(parts).strip()
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
        closer: str,
    ) -> List[str]:
        segments: List[str] = []
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
            if stripped[0] not in ("{", "[", '"'):
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

        result: List[str] = []
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
            "search_type": search_type,
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
            item.get("score", item.get("relevance", item.get("confidence"))),
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
            ),
        }

    def _normalize_recommendations_result(self, payload: Any) -> dict:
        summary = ""
        raw_recommendations = None

        if isinstance(payload, list):
            raw_recommendations = payload
        elif isinstance(payload, dict):
            summary = self._coerce_content_to_text(payload.get("summary", payload.get("message")))
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

        normalized: List[dict] = []
        for item in raw_recommendations or []:
            recommendation = self._normalize_recommendation_item(item)
            if recommendation:
                normalized.append(recommendation)

        if raw_recommendations and not normalized:
            raise AIJSONParseError("AI 返回了推荐数据，但没有可用的 website_id")

        return {
            "recommendations": normalized,
            "summary": summary,
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
            "description": description,
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

    def probe_text_output(self) -> dict:
        messages = [
            {
                "role": "system",
                "content": "You are a concise assistant.",
            },
            {
                "role": "user",
                "content": "Reply with exactly: test",
            },
        ]
        response = self._call_api(messages, temperature=0, max_tokens=32)
        text = self._cleanup_plain_text(self._extract_response_text(response))
        if not text:
            raise AIEmptyResponseError("模型没有返回可用文本")
        return {
            "text": text,
            "protocol": self.last_protocol_used or "",
            "attempts": list(self.last_attempt_trace),
        }

    def analyze_search_intent(self, user_query: str) -> dict:
        prompt = AI_SEARCH_PROMPT_TEMPLATE.format(user_query=user_query)
        messages = [
            {
                "role": "system",
                "content": "You are a website search assistant. Return JSON only.",
            },
            {"role": "user", "content": prompt},
        ]

        try:
            response = self._call_api(messages, max_tokens=300, expect_json=True)
            content = self._extract_response_text(response)
            intent_result = self._parse_json_response(content)
            return self._normalize_intent_result(intent_result, user_query)
        except Exception as exc:
            _get_logger().error(f"AI 意图分析失败: {str(exc)}")
            raise

    def recommend_websites(
        self,
        user_query: str,
        intent: dict,
        websites: List[dict],
        vector_scores: Optional[Dict[int, float]] = None,
        max_recommendations: int = 20,
    ) -> dict:
        websites_with_scores: List[dict] = []
        for website in websites:
            website_dict = website.copy() if isinstance(website, dict) else {
                "id": website.id if hasattr(website, "id") else website.get("id"),
                "title": website.title if hasattr(website, "title") else website.get("title", ""),
                "description": website.description if hasattr(website, "description") else website.get("description", ""),
                "category": website.category.name if hasattr(website, "category") and website.category else website.get("category", ""),
                "url": website.url if hasattr(website, "url") else website.get("url", ""),
            }
            if vector_scores and website_dict.get("id") in vector_scores:
                website_dict["vector_score"] = vector_scores[website_dict["id"]]
            websites_with_scores.append(website_dict)

        if vector_scores:
            websites_with_vector = [
                website for website in websites_with_scores if website.get("vector_score") is not None
            ]
            websites_without_vector = [
                website for website in websites_with_scores if website.get("vector_score") is None
            ]
            websites_with_vector.sort(
                key=lambda item: item.get("vector_score", 0),
                reverse=True,
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
            max_recommendations=max_recommendations,
        )

        messages = [
            {
                "role": "system",
                "content": "You are a website recommendation assistant. Return JSON only.",
            },
            {"role": "user", "content": prompt},
        ]

        try:
            response = self._call_api(messages, max_tokens=2000, expect_json=True)
            content = self._extract_response_text(response)
            parsed = self._parse_json_response(content)
            result = self._normalize_recommendations_result(parsed)
            if max_recommendations > 0:
                result["recommendations"] = result["recommendations"][:max_recommendations]
            return result
        except Exception as exc:
            _get_logger().error(f"AI 推荐失败: {str(exc)}")
            raise

    def translate_text(self, text: str, target_lang: str = "zh") -> str:
        if not text or not text.strip():
            return text

        lang_name = "中文" if target_lang == "zh" else target_lang
        prompt = (
            f"Please translate the following text into {lang_name}.\n"
            "Requirements:\n"
            "1. Preserve the original meaning accurately.\n"
            "2. Keep the result natural and concise.\n"
            "3. Return the translation only, with no extra explanation.\n\n"
            f"Source text:\n{text}"
        )

        messages = [
            {
                "role": "system",
                "content": "You are a professional translation assistant.",
            },
            {"role": "user", "content": prompt},
        ]

        try:
            response = self._call_api(messages, temperature=0.3, max_tokens=500)
            translated = self._extract_response_text(response)
            translated = self._cleanup_plain_text(translated).strip('"').strip("'").strip()
            if not translated:
                raise AIEmptyResponseError("AI 返回了空的翻译结果")
            return translated
        except Exception as exc:
            _get_logger().error(f"AI 翻译失败: {str(exc)}")
            raise Exception(f"翻译失败: {str(exc)}")

    def generate_website_info(self, url: str) -> dict:
        prompt = WEBSITE_INFO_PROMPT_TEMPLATE.format(url=url)
        messages = [
            {
                "role": "system",
                "content": "You generate concise website metadata and return JSON only.",
            },
            {"role": "user", "content": prompt},
        ]

        try:
            response = self._call_api(
                messages,
                temperature=0.5,
                max_tokens=300,
                expect_json=True,
            )
            content = self._extract_response_text(response)
            parsed = self._parse_json_response(content)
            return self._normalize_website_info_result(parsed)
        except Exception as exc:
            _get_logger().error(f"AI 生成网站信息失败: {str(exc)}")
            raise Exception(f"生成网站信息失败: {str(exc)}")

def _legacy_model_from_settings(settings, task: Optional[str] = None) -> Optional[str]:
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

    if task in {"intent", "rerank"}:
        return min(temperature, 0.2)

    return temperature


def get_ai_interface_mode(settings, provider=None) -> str:
    source = provider if provider is not None else settings
    value = (
        getattr(source, "interface_mode", None)
        or getattr(source, "ai_interface_mode", None)
        or ""
    ).strip().lower()
    if value not in AI_INTERFACE_MODE_VALUES:
        return AI_INTERFACE_MODE_AUTO
    return value


def _get_provider_recommended_models(provider) -> Dict[str, str]:
    if provider is None or not hasattr(provider, "get_recommended_models"):
        return {}
    models = provider.get_recommended_models()
    return models if isinstance(models, dict) else {}


def _get_provider_model_for_task(provider, task: Optional[str]) -> str:
    models = _get_provider_recommended_models(provider)
    if task and models.get(task):
        return models[task]
    if models.get("fallback"):
        return models["fallback"]
    if task and hasattr(provider, "get_model_catalog"):
        try:
            from app.utils.ai_model_discovery import select_task_models

            selected = select_task_models(provider.get_model_catalog() or [])
            if selected.get(task):
                return selected[task]
            if selected.get("fallback"):
                return selected["fallback"]
        except Exception:
            return ""
    return ""


def _build_runtime_entry(
    provider,
    model_name: str,
    task: Optional[str],
    source: str,
) -> Optional[Dict[str, Any]]:
    api_base_url = (getattr(provider, "api_base_url", None) or "").strip()
    api_key = (getattr(provider, "api_key", None) or "").strip()
    model_name = (model_name or "").strip()
    if not all([api_base_url, api_key, model_name]):
        return None

    return {
        "provider_id": getattr(provider, "id", None),
        "provider_name": (getattr(provider, "name", None) or "AI").strip(),
        "api_base_url": api_base_url,
        "api_key": api_key,
        "model_name": model_name,
        "interface_mode": get_ai_interface_mode(None, provider=provider),
        "task": task or "",
        "source": source,
    }


def resolve_ai_service_candidates(settings, task: Optional[str] = None) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    providers = []
    if hasattr(settings, "get_ai_providers"):
        try:
            providers = settings.get_ai_providers(enabled_only=False)
        except Exception:
            providers = []

    provider_map = {getattr(provider, "id", None): provider for provider in providers}
    bindings = settings.get_ai_task_bindings() if hasattr(settings, "get_ai_task_bindings") else {}
    binding = bindings.get(task, {}) if task in AI_TASK_KEYS else {}
    mode = (binding.get("mode") or "auto").strip().lower() if isinstance(binding, dict) else "auto"
    provider_id = binding.get("provider_id") if isinstance(binding, dict) else None
    model_name = (binding.get("model_name") or "").strip() if isinstance(binding, dict) else ""

    if provider_map:
        if task in AI_TASK_KEYS and mode == "manual":
            provider = provider_map.get(provider_id)
            if provider is None and hasattr(settings, "get_primary_ai_provider"):
                provider = settings.get_primary_ai_provider(enabled_only=True)
            entry = _build_runtime_entry(provider, model_name, task, "manual")
            if entry:
                candidates.append(entry)
        else:
            selected_providers: List[Any] = []
            if provider_id and provider_map.get(provider_id):
                selected_providers.append(provider_map[provider_id])
            else:
                selected_providers.extend(
                    provider for provider in providers if bool(getattr(provider, "enabled", True))
                )
            if not selected_providers:
                selected_providers.extend(providers)

            for provider in selected_providers:
                entry = _build_runtime_entry(
                    provider,
                    _get_provider_model_for_task(provider, task),
                    task,
                    "auto",
                )
                if entry:
                    candidates.append(entry)

    if not candidates:
        legacy_model = (
            model_name if mode == "manual" and model_name else _legacy_model_from_settings(settings, task=task)
        )
        api_base_url = (getattr(settings, "ai_api_base_url", None) or "").strip()
        api_key = (getattr(settings, "ai_api_key", None) or "").strip()
        if all([api_base_url, api_key, legacy_model]):
            candidates.append(
                {
                    "provider_id": None,
                    "provider_name": "Legacy AI",
                    "api_base_url": api_base_url,
                    "api_key": api_key,
                    "model_name": legacy_model,
                    "interface_mode": get_ai_interface_mode(settings),
                    "task": task or "",
                    "source": "legacy",
                }
            )

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for item in candidates:
        dedupe_key = (
            item.get("provider_id"),
            item.get("api_base_url"),
            item.get("model_name"),
            item.get("interface_mode"),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(item)

    return deduped


def get_ai_model_for_task(settings, task: Optional[str] = None) -> Optional[str]:
    candidates = resolve_ai_service_candidates(settings, task=task)
    if not candidates:
        return None
    return candidates[0].get("model_name")


def create_ai_service_from_settings(
    settings,
    require_enabled: bool = False,
    task: Optional[str] = None,
) -> Optional[AISearchService]:
    if require_enabled and not settings.ai_search_enabled:
        return None

    candidates = resolve_ai_service_candidates(settings, task=task)
    if not candidates:
        return None

    try:
        services: List[AISearchService] = []
        for candidate in candidates:
            service = AISearchService(
                api_base_url=candidate["api_base_url"],
                api_key=candidate["api_key"],
                model_name=candidate["model_name"],
                interface_mode=candidate["interface_mode"],
                temperature=get_ai_temperature_for_task(settings, task=task),
                max_tokens=settings.ai_max_tokens,
            )
            service.provider_id = candidate.get("provider_id")
            service.provider_name = candidate.get("provider_name", "")
            service.service_source = candidate.get("source", "")
            services.append(service)

        if len(services) == 1:
            return services[0]
        return AIFailoverService(services)
    except Exception as exc:
        _get_logger().error(f"创建 AI 服务失败: {str(exc)}")
        return None
