#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""辅助工具函数"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse


ICON_PROXY_SIZE = 128
ICON_FETCH_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}


BUILTIN_ICON_SOURCE_PROVIDERS = [
    {
        'id': 'origin_direct',
        'label': '原站直连',
        'kind': 'origin',
        'builtin': True,
        'enabled': True,
        'order': 10,
        'supports_download': True,
        'description': '优先解析网站自身声明的 favicon 与常见图标路径',
    },
    {
        'id': 'favicon_im',
        'label': 'favicon.im',
        'kind': 'proxy',
        'builtin': True,
        'enabled': True,
        'order': 20,
        'supports_download': True,
        'description': 'Cloudflare 加速代理，支持 larger 参数',
        'template': 'https://favicon.im/{domain}?larger=true',
    },
    {
        'id': 'vemetric',
        'label': 'Vemetric',
        'kind': 'proxy',
        'builtin': True,
        'enabled': True,
        'order': 30,
        'supports_download': True,
        'description': '支持尺寸与格式控制，也可自托管',
        'template': 'https://favicon.vemetric.com/{domain}?size={size}&format=png',
    },
    {
        'id': 'google_s2',
        'label': 'Google S2',
        'kind': 'proxy',
        'builtin': True,
        'enabled': True,
        'order': 40,
        'supports_download': True,
        'description': '经典稳定代理，支持尺寸参数',
        'template': 'https://www.google.com/s2/favicons?domain={domain}&sz={size}',
    },
    {
        'id': 'duckduckgo',
        'label': 'DuckDuckGo',
        'kind': 'proxy',
        'builtin': True,
        'enabled': True,
        'order': 50,
        'supports_download': True,
        'description': '隐私友好代理，返回 ico 图标',
        'template': 'https://icons.duckduckgo.com/ip3/{domain}.ico',
    },
    {
        'id': 'cccyun',
        'label': 'CCCYun',
        'kind': 'proxy',
        'builtin': True,
        'enabled': False,
        'order': 60,
        'supports_download': True,
        'description': '旧兼容代理，保留为末位兜底',
        'template': 'https://favicon.cccyun.cc/{domain}',
    },
]


def _extract_icon_host_variants(url):
    processed_url = (url or '').strip()
    if not processed_url:
        return []
    if not processed_url.startswith(('http://', 'https://')):
        processed_url = 'https://' + processed_url

    parsed = urlparse(processed_url)
    hostname = (parsed.hostname or parsed.netloc or '').lower().strip()
    if not hostname:
        return []

    variants = [hostname]
    if hostname.startswith('www.') and len(hostname) > 4:
        variants.append(hostname[4:])
    return list(dict.fromkeys(variants))


def get_default_icon_source_providers():
    return [provider.copy() for provider in BUILTIN_ICON_SOURCE_PROVIDERS]


def merge_icon_source_providers(raw_config):
    defaults = {provider['id']: provider.copy() for provider in BUILTIN_ICON_SOURCE_PROVIDERS}
    merged = []

    try:
        stored = raw_config
        if isinstance(raw_config, str):
            import json
            stored = json.loads(raw_config) if raw_config else []
        if not isinstance(stored, list):
            stored = []
    except Exception:
        stored = []

    for item in stored:
        if not isinstance(item, dict):
            continue

        provider_id = str(item.get('id') or '').strip()
        if not provider_id:
            continue

        if provider_id in defaults:
            provider = defaults.pop(provider_id)
            provider['enabled'] = bool(item.get('enabled', provider.get('enabled', True)))
            provider['order'] = int(item.get('order', provider.get('order', 999)))
            merged.append(provider)
        elif not item.get('builtin', True):
            merged.append({
                'id': provider_id,
                'label': str(item.get('label') or provider_id),
                'kind': str(item.get('kind') or 'proxy'),
                'builtin': False,
                'enabled': bool(item.get('enabled', True)),
                'order': int(item.get('order', 999)),
                'supports_download': bool(item.get('supports_download', True)),
                'description': str(item.get('description') or ''),
                'template': str(item.get('template') or ''),
            })

    merged.extend(defaults.values())
    merged.sort(key=lambda item: (int(item.get('order', 999)), item.get('label') or item.get('id') or ''))
    return merged


def _provider_url_for_domain(provider, domain, size: int = ICON_PROXY_SIZE):
    template = (provider.get('template') or '').strip()
    if not template:
        return None

    encoded_domain = quote(domain, safe='')
    return template.format(domain=encoded_domain, size=size, default='identicon')


def _fetch_origin_icon_candidates(url):
    processed_url = url
    if not processed_url.startswith(('http://', 'https://')):
        processed_url = 'https://' + processed_url

    parsed = urlparse(processed_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    candidates = []

    try:
        response = requests.get(processed_url, headers=ICON_FETCH_HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        for link in soup.find_all('link'):
            rel_values = [value.lower() for value in (link.get('rel') or []) if isinstance(value, str)]
            rel_text = ' '.join(rel_values)
            if 'icon' not in rel_text and 'apple-touch-icon' not in rel_text:
                continue
            href = (link.get('href') or '').strip()
            if not href:
                continue
            candidates.append(urljoin(base_url, href))
    except Exception:
        pass

    candidates.extend([
        urljoin(base_url, '/favicon.ico'),
        urljoin(base_url, '/favicon.png'),
        urljoin(base_url, '/apple-touch-icon.png'),
        urljoin(base_url, '/apple-touch-icon-precomposed.png'),
    ])
    return list(dict.fromkeys(candidates))


def build_icon_source_candidates(url, providers=None, preferred_provider: str | None = None, size: int = ICON_PROXY_SIZE):
    provider_list = merge_icon_source_providers(providers)
    preferred_provider = (preferred_provider or '').strip()
    force_specific_provider = preferred_provider and preferred_provider not in {'inherit', 'auto'}

    if force_specific_provider:
        provider_list = [provider for provider in provider_list if provider['id'] == preferred_provider]
    else:
        provider_list = [provider for provider in provider_list if provider.get('enabled', True)]

    candidates = []
    for provider in provider_list:
        if not force_specific_provider and not provider.get('enabled', True):
            continue

        if provider.get('kind') == 'origin':
            for candidate_url in _fetch_origin_icon_candidates(url):
                candidates.append({
                    'provider_id': provider['id'],
                    'provider_label': provider['label'],
                    'url': candidate_url,
                    'kind': provider.get('kind', 'origin'),
                })
            continue

        for domain in _extract_icon_host_variants(url):
            candidate_url = _provider_url_for_domain(provider, domain, size=size)
            if not candidate_url:
                continue
            candidates.append({
                'provider_id': provider['id'],
                'provider_label': provider['label'],
                'url': candidate_url,
                'kind': provider.get('kind', 'proxy'),
            })

    deduped = []
    seen = set()
    for candidate in candidates:
        key = candidate['url']
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def build_icon_candidate_urls(url, providers=None, preferred_provider: str | None = None, size: int = ICON_PROXY_SIZE):
    return [
        candidate['url']
        for candidate in build_icon_source_candidates(
            url,
            providers=providers,
            preferred_provider=preferred_provider,
            size=size,
        )
    ]


def _looks_like_icon_response(response, candidate_url):
    content_type = (response.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
    if content_type.startswith('image/'):
        return True

    suffix = Path(urlparse(candidate_url).path).suffix.lower()
    if suffix in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico'} and response.content:
        return True

    return False


def find_reachable_icon_candidate(url, providers=None, preferred_provider: str | None = None, size: int = ICON_PROXY_SIZE):
    candidates = build_icon_source_candidates(
        url,
        providers=providers,
        preferred_provider=preferred_provider,
        size=size,
    )
    errors = []

    for candidate in candidates:
        try:
            response = requests.get(candidate['url'], headers=ICON_FETCH_HEADERS, timeout=8)
            response.raise_for_status()
            if not response.content:
                raise ValueError('empty response body')
            if not _looks_like_icon_response(response, candidate['url']):
                raise ValueError(f'unexpected content type: {response.headers.get("Content-Type", "")}')
            return candidate, errors
        except Exception as exc:
            errors.append(f"{candidate['url']}: {exc}")

    return None, errors


def parse_website_info(url):
    """解析网站信息（标题、描述）"""
    try:
        processed_url = url
        if not processed_url.startswith(('http://', 'https://')):
            processed_url = 'https://' + processed_url
            
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        response = requests.get(processed_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        content_type = response.headers.get('content-type', '').lower()
        if 'charset=' in content_type:
            charset = content_type.split('charset=')[-1]
            response.encoding = charset
        else:
            content = response.content
            soup = BeautifulSoup(content, 'html.parser')
            meta_charset = soup.find('meta', charset=True)
            if meta_charset:
                response.encoding = meta_charset.get('charset')
            else:
                meta_content_type = soup.find('meta', {'http-equiv': lambda x: x and x.lower() == 'content-type'})
                if meta_content_type and 'charset=' in meta_content_type.get('content', '').lower():
                    charset = meta_content_type.get('content').lower().split('charset=')[-1]
                    response.encoding = charset
                elif 'charset=gb' in response.text.lower() or 'charset="gb' in response.text.lower():
                    response.encoding = 'gb18030'
                else:
                    response.encoding = response.apparent_encoding
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        title = ""
        if soup.title:
            title = soup.title.string.strip() if soup.title.string else ""
        if not title:
            h1 = soup.find('h1')
            if h1:
                title = h1.get_text().strip()
        
        description = ""
        meta_desc = soup.find('meta', attrs={'name': ['description', 'Description']})
        if meta_desc and meta_desc.get('content'):
            description = meta_desc.get('content').strip()
        
        if not description:
            for p in soup.find_all('p'):
                text = p.get_text().strip()
                if text and len(text) > 20:
                    description = text
                    break
        
        if description and len(description) > 200:
            description = description[:197] + "..."
            
        return {
            "success": True,
            "title": title,
            "description": description
        }
    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }


def get_website_icon(url, providers=None, preferred_provider: str | None = None):
    """获取网站图标"""
    try:
        processed_url = url
        if not processed_url.startswith(('http://', 'https://')):
            processed_url = 'http://' + processed_url
        candidates = build_icon_source_candidates(
            processed_url,
            providers=providers,
            preferred_provider=preferred_provider,
        )
        prefer_configured_sources = bool(providers) or (
            (preferred_provider or '').strip() not in {'', 'inherit', 'auto'}
        )

        if prefer_configured_sources:
            reachable_candidate, candidate_errors = find_reachable_icon_candidate(
                processed_url,
                providers=providers,
                preferred_provider=preferred_provider,
            )
            if reachable_candidate:
                return {
                    "success": True,
                    "message": "已按当前图标源配置返回候选地址",
                    "icon_url": reachable_candidate["url"],
                    "provider_id": reachable_candidate["provider_id"],
                    "provider_label": reachable_candidate["provider_label"],
                    "fallback_url": reachable_candidate["url"],
                    "candidates": [item["url"] for item in candidates],
                }
            if candidates:
                first_candidate = candidates[0]
                return {
                    "success": False,
                    "message": "图标源候选已生成，但暂未验证到可访问结果",
                    "provider_id": first_candidate["provider_id"],
                    "provider_label": first_candidate["provider_label"],
                    "fallback_url": first_candidate["url"],
                    "candidates": [item["url"] for item in candidates],
                    "errors": candidate_errors,
                }
            return {
                "success": False,
                "message": "当前图标源配置未生成可用候选地址",
                "fallback_url": None,
                "candidates": [],
            }

        headers = {
            'User-Agent': 'xiaoxiaoapi/1.0.0 (https://xxapi.cn)'
        }
        
        api_url = f"https://v2.xxapi.cn/api/ico?url={processed_url}"
        response = requests.get(api_url, headers=headers, timeout=5)
        
        try:
            result = response.json()
            if result.get('code') == 200 and 'data' in result:
                icon_url = result['data']
                return {
                    "success": True,
                    "icon_url": icon_url,
                    "provider_id": "xxapi",
                    "provider_label": "XXAPI",
                    "fallback_url": candidates[0]["url"] if candidates else None,
                    "candidates": [icon_url, *[item["url"] for item in candidates]],
                }
            else:
                error_msg = result.get('msg', '无法获取图标')
                return {
                    "success": False,
                    "message": error_msg,
                    "fallback_url": candidates[0]["url"] if candidates else None,
                    "provider_id": candidates[0]["provider_id"] if candidates else None,
                    "provider_label": candidates[0]["provider_label"] if candidates else None,
                    "candidates": [item["url"] for item in candidates],
                }
        except ValueError:
            if response.status_code == 200 and response.text:
                icon_url = response.text.strip()
                if icon_url.startswith('http'):
                    return {
                        "success": True,
                        "icon_url": icon_url,
                        "provider_id": "xxapi",
                        "provider_label": "XXAPI",
                        "fallback_url": candidates[0]["url"] if candidates else None,
                        "candidates": [icon_url, *[item["url"] for item in candidates]],
                    }
        
        if candidates:
            first_candidate = candidates[0]
            return {
                "success": True,
                "message": "已回退到内置图标源",
                "icon_url": first_candidate["url"],
                "provider_id": first_candidate["provider_id"],
                "provider_label": first_candidate["provider_label"],
                "fallback_url": first_candidate["url"],
                "candidates": [item["url"] for item in candidates],
            }

        return {
            "success": False,
            "message": "无法解析API返回内容",
            "fallback_url": None,
            "candidates": [],
        }
    except Exception as e:
        try:
            candidates = build_icon_source_candidates(
                processed_url,
                providers=providers,
                preferred_provider=preferred_provider,
            )
            if candidates:
                first_candidate = candidates[0]
                return {
                    "success": True,
                    "message": str(e),
                    "icon_url": first_candidate["url"],
                    "provider_id": first_candidate["provider_id"],
                    "provider_label": first_candidate["provider_label"],
                    "fallback_url": first_candidate["url"],
                    "candidates": [item["url"] for item in candidates],
                }
            return {
                "success": False,
                "message": str(e),
                "fallback_url": None,
                "candidates": [],
            }
        except:
            return {
                "success": False,
                "message": "URL解析失败",
                "fallback_url": None,
                "candidates": [],
            }

