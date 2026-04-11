#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 模型发现、探测与自动选型工具。"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from app.utils.ai_search import (
    AIEmptyResponseError,
    AISearchService,
)

NON_CHAT_MODEL_MARKERS = (
    "embedding",
    "image",
    "audio",
    "tts",
    "transcribe",
    "realtime",
    "moderation",
    "omni-moderation",
    "whisper",
    "dall",
    "sora",
)

LOW_PRIORITY_MODEL_MARKERS = (
    "thinking",
    "reasoning",
    "codex",
    "preview",
)


def compute_ai_probe_signature(
    api_base_url: str,
    api_key: str,
    interface_mode: str = "auto",
) -> Optional[str]:
    base_url = (api_base_url or "").strip().rstrip("/")
    api_key = (api_key or "").strip()
    interface_mode = (interface_mode or "auto").strip().lower()
    if not base_url or not api_key:
        return None

    digest = hashlib.sha256(
        f"{base_url}|{api_key}|{interface_mode}".encode("utf-8")
    ).hexdigest()
    return digest


def _normalize_model_item(item: dict) -> Optional[dict]:
    model_id = (item.get("id") or "").strip()
    if not model_id:
        return None

    model_id_lower = model_id.lower()
    probe_candidate = not any(marker in model_id_lower for marker in NON_CHAT_MODEL_MARKERS)

    return {
        "id": model_id,
        "owned_by": item.get("owned_by") or "",
        "probe_candidate": probe_candidate,
        "skip_reason": "" if probe_candidate else "非文本/聊天模型，已跳过主动探测",
    }


def _model_sort_key(model: dict) -> tuple[int, str]:
    model_id = (model.get("id") or "").lower()
    score = 0
    if any(marker in model_id for marker in LOW_PRIORITY_MODEL_MARKERS):
        score += 20
    if "mini" in model_id or "flash" in model_id:
        score -= 2
    if model.get("probe_candidate"):
        score -= 5
    return score, model_id


def list_provider_models(api_base_url: str, api_key: str, timeout: int = 20) -> List[dict]:
    url = f"{api_base_url.rstrip('/')}/v1/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()

    payload = response.json()
    raw_models = payload.get("data")
    if not isinstance(raw_models, list):
        raise ValueError("模型列表返回格式异常，缺少 data 数组")

    models = []
    for item in raw_models:
        if isinstance(item, dict):
            normalized = _normalize_model_item(item)
            if normalized:
                models.append(normalized)

    models.sort(key=_model_sort_key)
    return models


def _probe_text_output(service: AISearchService) -> str:
    messages = [
        {"role": "system", "content": "You are a concise assistant."},
        {"role": "user", "content": "Reply with exactly: hello"},
    ]
    response = service._call_api(messages, temperature=0, max_tokens=32)
    text = service._cleanup_plain_text(service._extract_response_text(response))
    if not text:
        raise AIEmptyResponseError("模型没有返回可用文本")
    return text


def _probe_json_output(service: AISearchService) -> dict:
    messages = [
        {"role": "system", "content": "请严格返回 JSON，不要输出其他文字。"},
        {
            "role": "user",
            "content": (
                '请返回一个 JSON 对象，格式为 '
                '{"intent":"test","keywords":["abc"]}'
            ),
        },
    ]
    response = service._call_api(
        messages,
        temperature=0,
        max_tokens=128,
        expect_json=True,
    )
    text = service._extract_response_text(response)
    payload = service._parse_json_response(text)
    return service._normalize_intent_result(payload, "测试")


def _format_probe_error(error: Exception) -> str:
    return str(error).strip() or error.__class__.__name__


def probe_model_capabilities(
    api_base_url: str,
    api_key: str,
    model_info: dict,
    interface_mode: str = "auto",
) -> dict:
    result = dict(model_info)
    result.update(
        {
            "supports_text": False,
            "supports_json": False,
            "supports_json_mode": False,
            "text_protocol": "",
            "json_protocol": "",
            "text_preview": "",
            "json_preview": "",
            "text_error": "",
            "json_error": "",
            "latency_ms": None,
            "probe_status": "skipped",
            "project_fit": "skipped",
            "suitability": {
                "translate": False,
                "intent": False,
                "rerank": False,
                "site_info": False,
            },
        }
    )

    if not model_info.get("probe_candidate"):
        return result

    latencies: List[int] = []
    service = AISearchService(
        api_base_url=api_base_url,
        api_key=api_key,
        model_name=model_info["id"],
        interface_mode=interface_mode,
        temperature=0.2,
        max_tokens=256,
    )

    text_started = time.perf_counter()
    try:
        text_preview = _probe_text_output(service)
        result["supports_text"] = True
        result["text_protocol"] = service.last_protocol_used or ""
        result["text_preview"] = text_preview[:120]
        latencies.append(int((time.perf_counter() - text_started) * 1000))
    except Exception as exc:
        result["text_error"] = _format_probe_error(exc)

    json_started = time.perf_counter()
    try:
        json_preview = _probe_json_output(service)
        result["supports_json"] = True
        result["supports_json_mode"] = bool(service._json_object_response_format_supported)
        result["json_protocol"] = service.last_protocol_used or ""
        result["json_preview"] = (
            f'intent={json_preview.get("intent", "")}; '
            f'keywords={",".join(json_preview.get("keywords", [])[:3])}'
        )
        latencies.append(int((time.perf_counter() - json_started) * 1000))
    except Exception as exc:
        result["json_error"] = _format_probe_error(exc)

    if latencies:
        result["latency_ms"] = int(sum(latencies) / len(latencies))

    result["suitability"] = {
        "translate": result["supports_text"],
        "intent": result["supports_json"],
        "rerank": result["supports_json"],
        "site_info": result["supports_json"],
    }

    if result["supports_text"] and result["supports_json"]:
        result["probe_status"] = "passed"
        result["project_fit"] = "good"
    elif result["supports_text"] or result["supports_json"]:
        result["probe_status"] = "partial"
        result["project_fit"] = "partial"
    else:
        result["probe_status"] = "failed"
        result["project_fit"] = "bad"

    return result


def summarize_probe_catalog(catalog: List[dict]) -> dict:
    stats = {
        "total_models": len(catalog or []),
        "candidate_models": 0,
        "compatible_models": 0,
        "partial_models": 0,
        "failed_models": 0,
        "skipped_models": 0,
    }

    for item in catalog or []:
        if item.get("probe_candidate"):
            stats["candidate_models"] += 1

        fit = item.get("project_fit")
        if fit == "good":
            stats["compatible_models"] += 1
        elif fit == "partial":
            stats["partial_models"] += 1
        elif fit == "bad":
            stats["failed_models"] += 1
        else:
            stats["skipped_models"] += 1

    return stats


def _score_model_for_task(model: dict, task: str, preferred_model: str = "") -> int:
    model_id = (model.get("id") or "").lower()
    score = 0

    if task == "translate" and model.get("supports_text"):
        score += 100
    elif task in {"intent", "rerank", "site_info"} and model.get("supports_json"):
        score += 100
    elif task == "fallback" and (model.get("supports_text") or model.get("supports_json")):
        score += 80

    if model.get("supports_json_mode"):
        score += 8

    preferred_protocol = ""
    if task == "translate":
        preferred_protocol = (model.get("text_protocol") or "").strip().lower()
    elif task in {"intent", "rerank", "site_info"}:
        preferred_protocol = (model.get("json_protocol") or "").strip().lower()

    protocol_bonus = {
        "chat": 6,
        "responses": 4,
        "chat_stream": 2,
    }
    score += protocol_bonus.get(preferred_protocol, 0)

    if preferred_model and model.get("id") == preferred_model:
        score += 10

    if "mini" in model_id:
        score += 6 if task in {"translate", "intent", "site_info"} else 2
    if "flash" in model_id:
        score += 4 if task in {"translate", "intent"} else 1

    if any(marker in model_id for marker in LOW_PRIORITY_MODEL_MARKERS):
        score -= 18

    latency_ms = model.get("latency_ms")
    if isinstance(latency_ms, int):
        score += max(0, 10 - min(latency_ms, 5000) // 500)

    return score


def _pick_best_model(catalog: List[dict], task: str, preferred_model: str = "") -> Optional[str]:
    eligible = []
    for item in catalog:
        suitability = item.get("suitability") or {}
        if task == "fallback":
            if not (item.get("supports_text") or item.get("supports_json")):
                continue
        elif not suitability.get(task):
            continue
        eligible.append(item)

    if not eligible:
        return None

    eligible.sort(
        key=lambda item: (
            -_score_model_for_task(item, task, preferred_model),
            item.get("latency_ms") if isinstance(item.get("latency_ms"), int) else 999999,
            item.get("id") or "",
        )
    )
    return eligible[0].get("id")


def select_task_models(catalog: List[dict], preferred_model: str = "") -> dict:
    selected = {
        "intent": _pick_best_model(catalog, "intent", preferred_model=preferred_model),
        "rerank": _pick_best_model(catalog, "rerank", preferred_model=preferred_model),
        "translate": _pick_best_model(catalog, "translate", preferred_model=preferred_model),
        "site_info": _pick_best_model(catalog, "site_info", preferred_model=preferred_model),
        "fallback": _pick_best_model(catalog, "fallback", preferred_model=preferred_model),
    }

    for task in ("intent", "rerank", "translate", "site_info"):
        if not selected[task]:
            selected[task] = selected["fallback"]

    return selected


def discover_and_probe_models(
    api_base_url: str,
    api_key: str,
    preferred_model: str = "",
    interface_mode: str = "auto",
    max_workers: int = 4,
) -> dict:
    catalog = list_provider_models(api_base_url, api_key)
    probed: Dict[str, dict] = {}
    candidates = [item for item in catalog if item.get("probe_candidate")]

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(candidates) or 1))) as executor:
        future_map = {
            executor.submit(
                probe_model_capabilities,
                api_base_url,
                api_key,
                item,
                interface_mode,
            ): item["id"]
            for item in candidates
        }

        for future in as_completed(future_map):
            model_id = future_map[future]
            try:
                probed[model_id] = future.result()
            except Exception as exc:
                probed[model_id] = {
                    "id": model_id,
                    "owned_by": "",
                    "probe_candidate": True,
                    "skip_reason": "",
                    "supports_text": False,
                    "supports_json": False,
                    "supports_json_mode": False,
                    "text_protocol": "",
                    "json_protocol": "",
                    "text_preview": "",
                    "json_preview": "",
                    "text_error": _format_probe_error(exc),
                    "json_error": "",
                    "latency_ms": None,
                    "probe_status": "failed",
                    "project_fit": "bad",
                    "suitability": {
                        "translate": False,
                        "intent": False,
                        "rerank": False,
                        "site_info": False,
                    },
                }

    merged_catalog = []
    for item in catalog:
        merged_catalog.append(probed.get(item["id"], {
            **item,
            "supports_text": False,
            "supports_json": False,
            "supports_json_mode": False,
            "text_protocol": "",
            "json_protocol": "",
            "text_preview": "",
            "json_preview": "",
            "text_error": "",
            "json_error": "",
            "latency_ms": None,
            "probe_status": "skipped",
            "project_fit": "skipped",
            "suitability": {
                "translate": False,
                "intent": False,
                "rerank": False,
                "site_info": False,
            },
        }))

    return {
        "catalog": merged_catalog,
        "selected_models": select_task_models(merged_catalog, preferred_model=preferred_model),
        "stats": summarize_probe_catalog(merged_catalog),
        "probe_last_at": datetime.utcnow().isoformat(),
        "probe_signature": compute_ai_probe_signature(
            api_base_url,
            api_key,
            interface_mode=interface_mode,
        ),
        "interface_mode": (interface_mode or "auto"),
    }
