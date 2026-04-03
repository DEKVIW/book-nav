#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Icon management service with legacy Website.icon compatibility."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests
from flask import current_app, g, has_app_context, has_request_context, url_for
from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import joinedload

from app import db
from app.models import IconAsset, IconSyncTask, SiteSettings, Website, WebsiteIcon


ICON_TASK_SYNC_MISSING = 'sync_missing'
ICON_TASK_SYNC_LOCAL = 'sync_local'
ICON_TASK_SYNC_IMAGEBED = 'sync_imagebed'
ICON_TASK_RETRY_FAILED = 'retry_failed'
ICON_TASK_REFRESH_SOURCE = 'refresh_source'

IMAGEBED_PROVIDER_EASYIMAGE = 'easyimage'

ALLOWED_ICON_EXTENSIONS = {'.png', '.jpg', '.gif', '.webp', '.svg', '.ico'}
CONTENT_TYPE_EXTENSION_MAP = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/svg+xml': '.svg',
    'image/x-icon': '.ico',
    'image/vnd.microsoft.icon': '.ico',
}

REQUEST_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    )
}

ICON_TASK_STALE_GRACE_SECONDS = 180
ICON_PROXY_HOSTS = {
    'favicon.im',
    'favicon.vemetric.com',
    'www.google.com',
    'icons.duckduckgo.com',
    'favicon.cccyun.cc',
}
_icon_task_threads: dict[int, threading.Thread] = {}


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime('%Y-%m-%d %H:%M:%S') if dt else None


def _format_elapsed(seconds: float | int | None) -> str:
    if not seconds:
        return '0s'
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours}h {minutes}m {seconds}s'
    if minutes:
        return f'{minutes}m {seconds}s'
    return f'{seconds}s'


def _task_reference_time(task: IconSyncTask | None) -> datetime | None:
    if not task:
        return None
    return task.updated_at or task.started_at or task.created_at


def _is_registered_task_alive(task_id: int | None) -> bool:
    if not task_id:
        return False
    worker = _icon_task_threads.get(task_id)
    if not worker:
        return False
    if worker.is_alive():
        return True
    _icon_task_threads.pop(task_id, None)
    return False


def _is_sqlite_locked_error(exc: Exception) -> bool:
    return 'database is locked' in str(exc).lower()


def _commit_db_session(max_attempts: int = 5, base_delay: float = 0.2) -> None:
    for attempt in range(max_attempts):
        try:
            db.session.commit()
            return
        except OperationalError as exc:
            db.session.rollback()
            if not _is_sqlite_locked_error(exc) or attempt == max_attempts - 1:
                raise
            time.sleep(base_delay * (attempt + 1))


def _normalize_input_url(url: str | None, default_scheme: str = 'https') -> str:
    url = (url or '').strip()
    if not url:
        return ''
    if '://' not in url:
        url = f'{default_scheme}://{url}'
    return url


def _extract_host(url: str | None) -> str:
    normalized = _normalize_input_url(url)
    if not normalized:
        return ''
    parsed = urlparse(normalized)
    return (parsed.hostname or parsed.netloc or '').lower().strip()


def _extract_domain_key(url: str | None) -> str:
    host = _extract_host(url)
    if host.startswith('www.'):
        host = host[4:]
    return host


def _extract_icon_host_variants(url: str | None) -> list[str]:
    host = _extract_host(url)
    if not host:
        return []
    variants = [host]
    if host.startswith('www.') and len(host) > 4:
        variants.append(host[4:])
    return list(dict.fromkeys(variants))


def _provider_url_for_domain(provider: dict[str, Any], domain: str, size: int = 128) -> str | None:
    template = (provider.get('template') or '').strip()
    if not template:
        return None
    return template.format(domain=quote(domain, safe=''), size=size, default='identicon')


def _get_site_settings_cached() -> SiteSettings:
    if has_app_context():
        cached = getattr(g, '_icon_site_settings', None)
        if cached is None:
            cached = SiteSettings.get_settings()
            g._icon_site_settings = cached
        return cached
    return SiteSettings.get_settings()


def _get_source_provider_configs() -> list[dict[str, Any]]:
    if has_app_context():
        cached = getattr(g, '_icon_source_provider_configs', None)
        if cached is not None:
            return cached
    try:
        providers = _get_site_settings_cached().get_icon_source_providers()
    except Exception:
        providers = []
    if has_app_context():
        g._icon_source_provider_configs = providers
    return providers


def _get_source_provider_map() -> dict[str, dict[str, Any]]:
    if has_app_context():
        cached = getattr(g, '_icon_source_provider_map', None)
        if cached is not None:
            return cached
    provider_map = {
        provider.get('id'): provider
        for provider in _get_source_provider_configs()
        if provider.get('id')
    }
    if has_app_context():
        g._icon_source_provider_map = provider_map
    return provider_map


def _get_preferred_source_provider(meta: WebsiteIcon | None) -> str | None:
    if not meta:
        return None
    value = (meta.source_provider_override or '').strip()
    return value or None


def _find_source_provider_by_id(provider_id: str | None) -> dict[str, Any] | None:
    provider_id = (provider_id or '').strip()
    if not provider_id:
        return None
    return _get_source_provider_map().get(provider_id)


def _get_source_provider_label(provider_id: str | None) -> str | None:
    provider = _find_source_provider_by_id(provider_id)
    if provider:
        return provider.get('label') or provider.get('id')
    return provider_id or None


def _match_source_provider(website: Website, source_url: str | None, meta: WebsiteIcon | None = None) -> str | None:
    source_url = (source_url or '').strip()
    if not source_url:
        return None

    provider_configs = _get_source_provider_configs()
    for provider in provider_configs:
        if provider.get('kind') == 'origin':
            continue
        for domain in _extract_icon_host_variants(website.url):
            candidate_url = _provider_url_for_domain(provider, domain)
            if candidate_url and candidate_url == source_url:
                return provider.get('id')

    source_host = _extract_host(source_url)
    provider_host_map = {
        'www.google.com': 'google_s2',
        'icons.duckduckgo.com': 'duckduckgo',
        'favicon.cccyun.cc': 'cccyun',
        'favicon.im': 'favicon_im',
        'favicon.vemetric.com': 'vemetric',
    }
    return provider_host_map.get(source_host, 'origin_direct')


def _should_allow_domain_reuse(source_url: str | None, domain_key: str | None) -> bool:
    if not domain_key:
        return False
    source_host = _extract_host(source_url)
    source_domain = _extract_domain_key(source_url)
    return source_domain == domain_key or source_host in ICON_PROXY_HOSTS


def _build_icon_download_candidates(website: Website, primary_source_url: str | None) -> list[str]:
    candidates: list[str] = []
    meta = website.icon_meta
    preferred_provider = _get_preferred_source_provider(meta)
    force_specific_provider = preferred_provider not in {None, '', 'inherit', 'auto'}
    allow_primary_source = True

    if force_specific_provider and meta and meta.source_mode == 'auto':
        allow_primary_source = (
            _match_source_provider(website, primary_source_url, meta) == preferred_provider
        )

    for candidate in [primary_source_url] if allow_primary_source else []:
        candidate = (candidate or '').strip()
        if candidate:
            candidates.append(candidate)

    try:
        from app.main.utils import build_icon_candidate_urls

        candidates.extend(
            build_icon_candidate_urls(
                website.url,
                providers=_get_source_provider_configs(),
                preferred_provider=preferred_provider,
            )
        )
    except Exception:
        pass

    deduped: list[str] = []
    for candidate in candidates:
        candidate = (candidate or '').strip()
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _download_icon_response(website: Website, source_url: str) -> tuple[str, requests.Response]:
    errors: list[str] = []
    for candidate_url in _build_icon_download_candidates(website, source_url):
        try:
            response = requests.get(candidate_url, headers=REQUEST_HEADERS, timeout=15)
            response.raise_for_status()
            if not response.content:
                raise ValueError('empty icon response')
            return candidate_url, response
        except Exception as exc:
            errors.append(f'{candidate_url}: {exc}')

    if errors:
        raise RuntimeError(' | '.join(errors[:3]))
    raise RuntimeError('missing icon source url')


def _public_static_url(relative_path: str | None) -> str | None:
    if not relative_path:
        return None
    relative_path = str(relative_path).replace('\\', '/').lstrip('/')
    if has_request_context():
        return url_for('static', filename=relative_path)
    return f'/static/{relative_path}'


def _absolute_static_path(relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    relative_path = str(relative_path).replace('/', os.sep).replace('\\', os.sep).lstrip('/\\')
    return Path(current_app.root_path) / 'static' / relative_path


def _asset_absolute_path(asset: IconAsset | None) -> Path | None:
    if not asset or not asset.local_path:
        return None
    return _absolute_static_path(asset.local_path)


def _asset_file_exists(asset: IconAsset | None) -> bool:
    path = _asset_absolute_path(asset)
    return bool(path and path.exists() and path.is_file())


def _guess_extension(content_type: str | None, filename_hint: str | None) -> str:
    normalized = (content_type or '').split(';', 1)[0].strip().lower()
    if normalized in CONTENT_TYPE_EXTENSION_MAP:
        return CONTENT_TYPE_EXTENSION_MAP[normalized]

    suffix = Path(filename_hint or '').suffix.lower()
    if suffix in ALLOWED_ICON_EXTENSIONS:
        return suffix

    guessed = mimetypes.guess_extension(normalized or '') or ''
    if guessed == '.jpe':
        guessed = '.jpg'
    if guessed in ALLOWED_ICON_EXTENSIONS:
        return guessed

    return '.png'


def _read_json(payload_text: str) -> Any:
    try:
        return json.loads(payload_text)
    except Exception:
        return None


def _find_in_payload(payload: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = key.lower()
            if lowered in keys and isinstance(value, str) and value.strip():
                return value.strip()
            found = _find_in_payload(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_in_payload(item, keys)
            if found:
                return found
    return None


def _extract_imagebed_urls(payload: Any) -> tuple[str | None, str | None]:
    image_url = _find_in_payload(payload, ('url', 'imageurl', 'imgurl'))
    delete_url = _find_in_payload(payload, ('delete', 'delete_url', 'deleteurl'))
    return image_url, delete_url


def ensure_website_icon(website: Website) -> WebsiteIcon:
    meta = website.icon_meta
    if not meta:
        meta = WebsiteIcon(
            website=website,
            domain_key=_extract_domain_key(website.url),
            fetch_status='success' if (website.icon or '').strip() else 'pending',
        )
        db.session.add(meta)
        db.session.flush()
    elif not meta.domain_key:
        meta.domain_key = _extract_domain_key(website.url)
    return meta


def get_local_icon_url(meta: WebsiteIcon | None) -> str | None:
    if not meta or not meta.icon_asset or not meta.icon_asset.local_path:
        return None
    if not _asset_file_exists(meta.icon_asset):
        return None
    return _public_static_url(meta.icon_asset.local_path)


def get_imagebed_icon_url(meta: WebsiteIcon | None) -> str | None:
    if not meta or not meta.icon_asset:
        return None
    imagebed_url = (meta.icon_asset.imagebed_url or '').strip()
    return imagebed_url or None


def _get_effective_display_mode(meta: WebsiteIcon | None) -> str:
    settings = _get_site_settings_cached()
    if meta and meta.display_mode_override and meta.display_mode_override != 'inherit':
        return meta.display_mode_override
    return (settings.icon_display_mode or 'smart').strip() or 'smart'


def should_sync_local(meta: WebsiteIcon | None) -> bool:
    settings = _get_site_settings_cached()
    mode = (meta.sync_local_mode if meta else 'inherit') or 'inherit'
    if mode == 'always':
        return True
    if mode == 'never':
        return False
    return bool(settings.icon_default_sync_local)


def should_sync_imagebed(meta: WebsiteIcon | None) -> bool:
    settings = _get_site_settings_cached()
    mode = (meta.sync_imagebed_mode if meta else 'inherit') or 'inherit'
    if mode == 'always':
        return True
    if mode == 'never':
        return False
    return bool(settings.icon_default_sync_imagebed)


def _select_preferred_asset(assets: list[IconAsset]) -> IconAsset | None:
    if not assets:
        return None

    def score(asset: IconAsset) -> tuple[int, int, int, str]:
        local_score = 1 if _asset_file_exists(asset) else 0
        imagebed_score = 1 if (asset.imagebed_url or '').strip() else 0
        source_score = 1 if (asset.source_url or '').strip() else 0
        return (local_score, imagebed_score, source_score, _iso(asset.updated_at) or '')

    return max(assets, key=score)


def _find_domain_reusable_asset(domain_key: str | None) -> IconAsset | None:
    if not domain_key:
        return None
    assets = (
        IconAsset.query.join(WebsiteIcon, WebsiteIcon.icon_asset_id == IconAsset.id)
        .filter(WebsiteIcon.domain_key == domain_key)
        .all()
    )
    return _select_preferred_asset(assets)


def _find_reusable_asset(
    *,
    source_url: str | None = None,
    domain_key: str | None = None,
    file_hash: str | None = None,
    allow_domain_reuse: bool = True,
) -> IconAsset | None:
    if source_url:
        asset = IconAsset.query.filter_by(source_url=source_url).first()
        if asset:
            return asset
    if file_hash:
        asset = IconAsset.query.filter_by(file_hash=file_hash).first()
        if asset:
            return asset
    if allow_domain_reuse and domain_key:
        return _find_domain_reusable_asset(domain_key)
    return None


def _cleanup_orphan_asset(asset: IconAsset | None) -> None:
    if not asset:
        return
    if asset.website_icons.count():
        return

    path = _asset_absolute_path(asset)
    if path and path.exists():
        try:
            path.unlink()
        except OSError:
            pass

    db.session.delete(asset)


def _bind_asset_to_meta(meta: WebsiteIcon, asset: IconAsset | None) -> None:
    previous_asset = meta.icon_asset
    if previous_asset and asset and previous_asset.id == asset.id:
        return

    meta.icon_asset = asset
    if asset and asset.domain_key and not meta.domain_key:
        meta.domain_key = asset.domain_key
    db.session.flush()

    if previous_asset and (not asset or previous_asset.id != asset.id):
        _cleanup_orphan_asset(previous_asset)


def _mark_reused_asset(meta: WebsiteIcon, asset: IconAsset | None) -> None:
    if not asset:
        return
    if asset.source_url and not meta.website.icon:
        meta.website.icon = asset.source_url
    if asset.source_url or meta.website.icon:
        meta.fetch_status = 'success'
    if _asset_file_exists(asset):
        meta.local_status = 'success'
    if asset.imagebed_url:
        meta.imagebed_status = 'success'
    meta.last_error = None


def _persist_icon_bytes(
    website: Website,
    content: bytes,
    content_type: str | None,
    filename_hint: str | None,
    source_url: str | None = None,
) -> IconAsset:
    if not content:
        raise ValueError('empty icon content')

    file_hash = hashlib.sha256(content).hexdigest()
    existing_asset = _find_reusable_asset(file_hash=file_hash, allow_domain_reuse=False)
    domain_key = _extract_domain_key(website.url)
    extension = _guess_extension(content_type, filename_hint or source_url)
    relative_path = f'uploads/site_icons/assets/{file_hash[:2]}/{file_hash}{extension}'
    absolute_path = _absolute_static_path(relative_path)
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    if existing_asset:
        if not existing_asset.local_path:
            existing_asset.local_path = relative_path
        if absolute_path and not absolute_path.exists():
            absolute_path.write_bytes(content)
        if source_url and not existing_asset.source_url:
            existing_asset.source_url = source_url
        if source_url and not existing_asset.source_host:
            existing_asset.source_host = _extract_host(source_url)
        if not existing_asset.domain_key:
            existing_asset.domain_key = domain_key
        if not existing_asset.mime_type and content_type:
            existing_asset.mime_type = content_type.split(';', 1)[0]
        db.session.flush()
        return existing_asset

    absolute_path.write_bytes(content)
    asset = IconAsset(
        domain_key=domain_key,
        file_hash=file_hash,
        source_url=source_url,
        source_host=_extract_host(source_url),
        local_path=relative_path,
        mime_type=(content_type or '').split(';', 1)[0] or None,
    )
    db.session.add(asset)
    db.session.flush()
    return asset


def _resolve_source_url(meta: WebsiteIcon | None, website: Website) -> str | None:
    if meta and meta.source_mode == 'manual_upload':
        return None
    if (website.icon or '').strip():
        return website.icon.strip()
    if meta and meta.icon_asset and (meta.icon_asset.source_url or '').strip():
        return meta.icon_asset.source_url.strip()
    return None


def get_website_icon_snapshot(website: Website) -> dict[str, Any]:
    meta = website.icon_meta
    asset = meta.icon_asset if meta else None

    effective_display_mode = _get_effective_display_mode(meta)
    source_mode = meta.source_mode if meta else ('manual_url' if (website.icon or '').strip() else 'auto')
    source_provider_override = (meta.source_provider_override if meta else 'inherit') or 'inherit'

    source_url = None if source_mode == 'manual_upload' else _resolve_source_url(meta, website)
    source_provider = _match_source_provider(website, source_url, meta) if source_url else None
    local_url = get_local_icon_url(meta)
    imagebed_url = get_imagebed_icon_url(meta)

    order_map = {
        'source': [('source', source_url), ('local', local_url), ('imagebed', imagebed_url)],
        'local': [('local', local_url), ('imagebed', imagebed_url), ('source', source_url)],
        'imagebed': [('imagebed', imagebed_url), ('local', local_url), ('source', source_url)],
        'smart': [('local', local_url), ('imagebed', imagebed_url), ('source', source_url)],
    }

    selected_origin = None
    selected_url = None
    for origin, candidate_url in order_map.get(effective_display_mode, order_map['smart']):
        if candidate_url:
            selected_origin = origin
            selected_url = candidate_url
            break

    if source_mode == 'manual_upload' and selected_origin == 'local':
        selected_origin = 'manual_upload'

    asset_ref_count = getattr(asset, '_website_icon_ref_count', None) if asset else 0
    shared_asset = bool(asset and asset_ref_count and asset_ref_count > 1)

    return {
        'url': selected_url,
        'origin': selected_origin,
        'display_mode': effective_display_mode,
        'source_mode': source_mode,
        'source_provider_override': source_provider_override,
        'source_provider': source_provider,
        'source_provider_label': _get_source_provider_label(source_provider),
        'fetch_status': meta.fetch_status if meta else ('success' if source_url else 'pending'),
        'local_status': meta.local_status if meta else 'pending',
        'imagebed_status': meta.imagebed_status if meta else 'pending',
        'source_url': source_url,
        'local_url': local_url,
        'imagebed_url': imagebed_url,
        'has_source': bool(source_url),
        'has_local': bool(local_url),
        'has_imagebed': bool(imagebed_url),
        'last_error': meta.last_error if meta else None,
        'asset_id': asset.id if asset else None,
        'domain_key': (meta.domain_key if meta else '') or _extract_domain_key(website.url),
        'shared_asset': shared_asset,
        'asset_ref_count': asset_ref_count if asset_ref_count is not None else (1 if asset else 0),
    }


def resolve_display_icon_url(website: Website) -> str | None:
    return get_website_icon_snapshot(website).get('url')


def save_manual_icon_upload(
    website: Website,
    file_storage: Any,
    sync_imagebed: bool | None = None,
) -> dict[str, Any]:
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return get_website_icon_snapshot(website)

    meta = ensure_website_icon(website)
    content = file_storage.read()
    try:
        file_storage.stream.seek(0)
    except Exception:
        pass

    asset = _persist_icon_bytes(
        website,
        content,
        getattr(file_storage, 'content_type', None),
        getattr(file_storage, 'filename', None),
        None,
    )
    _bind_asset_to_meta(meta, asset)

    local_url = get_local_icon_url(meta)
    website.icon = local_url or website.icon or ''
    meta.source_mode = 'manual_upload'
    meta.fetch_status = 'success'
    meta.local_status = 'success'
    meta.last_fetch_at = _now()
    meta.last_local_sync_at = _now()
    meta.last_error = None
    db.session.commit()

    if sync_imagebed is None:
        sync_imagebed = should_sync_imagebed(meta)
    if sync_imagebed:
        try:
            upload_icon_to_imagebed(website)
        except Exception:
            pass

    return get_website_icon_snapshot(website)


def download_icon_to_local(
    website: Website,
    source_url: str,
    update_legacy_icon: bool = True,
) -> dict[str, Any]:
    source_url = (source_url or '').strip()
    if not source_url:
        raise ValueError('missing icon source url')

    meta = ensure_website_icon(website)
    domain_key = meta.domain_key or _extract_domain_key(website.url)
    reusable_asset = _find_reusable_asset(
        source_url=source_url,
        domain_key=domain_key,
        allow_domain_reuse=_should_allow_domain_reuse(source_url, domain_key),
    )

    meta.fetch_status = 'success'
    meta.last_fetch_at = _now()

    if reusable_asset and _asset_file_exists(reusable_asset):
        _bind_asset_to_meta(meta, reusable_asset)
        _mark_reused_asset(meta, reusable_asset)
        meta.local_status = 'success'
        meta.last_local_sync_at = _now()
        meta.last_error = None
        if update_legacy_icon and meta.source_mode != 'manual_upload':
            website.icon = reusable_asset.source_url or source_url
        db.session.commit()
        return get_website_icon_snapshot(website)

    try:
        effective_source_url, response = _download_icon_response(website, source_url)
        asset = _persist_icon_bytes(
            website,
            response.content,
            response.headers.get('Content-Type'),
            Path(urlparse(effective_source_url).path).name,
            effective_source_url,
        )
        asset.source_url = effective_source_url
        asset.source_host = _extract_host(effective_source_url)
        _bind_asset_to_meta(meta, asset)

        meta.local_status = 'success'
        meta.last_local_sync_at = _now()
        meta.last_error = None
        if update_legacy_icon and meta.source_mode != 'manual_upload':
            website.icon = effective_source_url
        db.session.commit()
        return get_website_icon_snapshot(website)
    except Exception as exc:
        meta.local_status = 'failed'
        meta.last_error = str(exc)
        db.session.commit()
        raise


def upload_icon_to_imagebed(website: Website) -> dict[str, Any]:
    meta = ensure_website_icon(website)
    asset = meta.icon_asset
    if asset and (asset.imagebed_url or '').strip():
        meta.imagebed_status = 'success'
        meta.last_error = None
        db.session.commit()
        return get_website_icon_snapshot(website)

    if not asset or not _asset_file_exists(asset):
        snapshot = get_website_icon_snapshot(website)
        source_url = snapshot.get('source_url')
        if source_url:
            download_icon_to_local(website, source_url)
            meta = ensure_website_icon(website)
            asset = meta.icon_asset

    if not asset or not _asset_file_exists(asset):
        meta.imagebed_status = 'failed'
        meta.last_error = 'missing local icon asset'
        db.session.commit()
        raise ValueError('missing local icon asset')

    provider, api_url, token = _get_site_settings_cached().get_icon_imagebed_config()
    if provider != IMAGEBED_PROVIDER_EASYIMAGE or not api_url or not token:
        meta.imagebed_status = 'failed'
        meta.last_error = 'imagebed is not configured'
        db.session.commit()
        raise ValueError('imagebed is not configured')

    local_path = _asset_absolute_path(asset)
    try:
        with local_path.open('rb') as handle:
            response = requests.post(
                api_url,
                data={'token': token},
                files={'image': (local_path.name, handle, asset.mime_type or 'application/octet-stream')},
                timeout=30,
            )
        response.raise_for_status()
        payload_text = response.text
        payload = _read_json(payload_text)
        image_url, delete_url = _extract_imagebed_urls(payload)
        if not image_url:
            raise ValueError('imagebed response missing image url')

        asset.imagebed_provider = provider
        asset.imagebed_url = image_url
        asset.imagebed_delete_url = delete_url
        asset.imagebed_payload_json = payload_text

        meta.imagebed_status = 'success'
        meta.last_imagebed_sync_at = _now()
        meta.last_error = None
        db.session.commit()
        return get_website_icon_snapshot(website)
    except Exception as exc:
        meta.imagebed_status = 'failed'
        meta.last_error = str(exc)
        db.session.commit()
        raise


def mark_icon_manual_url(
    website: Website,
    icon_url: str,
    sync_local: bool | None = None,
    sync_imagebed: bool | None = None,
) -> dict[str, Any]:
    icon_url = (icon_url or '').strip()
    if not icon_url:
        raise ValueError('missing icon url')

    meta = ensure_website_icon(website)
    old_asset = meta.icon_asset

    website.icon = icon_url
    meta.source_mode = 'manual_url'
    meta.fetch_status = 'success'
    meta.last_fetch_at = _now()
    meta.last_error = None

    reusable_asset = _find_reusable_asset(
        source_url=icon_url,
        domain_key=meta.domain_key,
        allow_domain_reuse=_should_allow_domain_reuse(icon_url, meta.domain_key),
    )
    if reusable_asset:
        _bind_asset_to_meta(meta, reusable_asset)
        _mark_reused_asset(meta, reusable_asset)
    else:
        if old_asset:
            _bind_asset_to_meta(meta, None)
        meta.local_status = 'pending'
        meta.imagebed_status = 'pending'

    db.session.commit()

    if sync_local is None:
        sync_local = should_sync_local(meta)
    if sync_local and not get_local_icon_url(meta):
        download_icon_to_local(website, icon_url)
        meta = ensure_website_icon(website)

    if sync_imagebed is None:
        sync_imagebed = should_sync_imagebed(meta)
    if sync_imagebed and not get_imagebed_icon_url(meta):
        if not get_local_icon_url(meta):
            download_icon_to_local(website, icon_url)
        upload_icon_to_imagebed(website)

    return get_website_icon_snapshot(website)


def refresh_icon_from_source(
    website: Website,
    force: bool = False,
    sync_local: bool | None = None,
    sync_imagebed: bool | None = None,
) -> dict[str, Any]:
    meta = ensure_website_icon(website)

    if meta.source_mode == 'manual_upload':
        snapshot = get_website_icon_snapshot(website)
        if sync_imagebed is None:
            sync_imagebed = should_sync_imagebed(meta)
        if sync_imagebed and not snapshot.get('has_imagebed'):
            upload_icon_to_imagebed(website)
        return get_website_icon_snapshot(website)

    source_url = None
    if meta.source_mode == 'manual_url':
        source_url = (website.icon or '').strip()
    elif not force and (website.icon or '').strip():
        source_url = website.icon.strip()

    if not source_url:
        from app.main.utils import get_website_icon
        result = get_website_icon(
            website.url,
            providers=_get_source_provider_configs(),
            preferred_provider=_get_preferred_source_provider(meta),
        )
        source_url = (result.get('icon_url') or '').strip()
        if not source_url and result.get('success'):
            source_url = (result.get('fallback_url') or '').strip()

    if not source_url:
        meta.fetch_status = 'failed'
        meta.last_error = (
            (result.get('message') if 'result' in locals() and isinstance(result, dict) else '')
            or 'unable to resolve icon source'
        )
        db.session.commit()
        return get_website_icon_snapshot(website)

    website.icon = source_url
    meta.fetch_status = 'success'
    meta.last_fetch_at = _now()
    meta.last_error = None

    reusable_asset = _find_reusable_asset(
        source_url=source_url,
        domain_key=meta.domain_key,
        allow_domain_reuse=_should_allow_domain_reuse(source_url, meta.domain_key),
    )
    if reusable_asset:
        _bind_asset_to_meta(meta, reusable_asset)
        _mark_reused_asset(meta, reusable_asset)
    db.session.commit()

    if sync_local is None:
        sync_local = should_sync_local(meta)
    if sync_local and not get_local_icon_url(meta):
        try:
            download_icon_to_local(website, source_url)
        except Exception:
            pass
        meta = ensure_website_icon(website)

    if sync_imagebed is None:
        sync_imagebed = should_sync_imagebed(meta)
    if sync_imagebed and not get_imagebed_icon_url(meta):
        if not get_local_icon_url(meta):
            try:
                download_icon_to_local(website, source_url)
            except Exception:
                pass
        try:
            upload_icon_to_imagebed(website)
        except Exception:
            pass

    return get_website_icon_snapshot(website)


def sync_icon_after_save(
    website: Website,
    *,
    uploaded_file: Any = None,
    icon_url: str | None = None,
    auto_fetch: bool = False,
    sync_local: bool | None = None,
    sync_imagebed: bool | None = None,
    source_provider_override: str | None = None,
    display_mode_override: str | None = None,
    sync_local_mode: str | None = None,
    sync_imagebed_mode: str | None = None,
) -> dict[str, Any]:
    meta = ensure_website_icon(website)

    if source_provider_override is not None:
        meta.source_provider_override = source_provider_override
    if display_mode_override:
        meta.display_mode_override = display_mode_override
    if sync_local_mode:
        meta.sync_local_mode = sync_local_mode
    if sync_imagebed_mode:
        meta.sync_imagebed_mode = sync_imagebed_mode
    meta.domain_key = _extract_domain_key(website.url)
    db.session.commit()

    if uploaded_file and getattr(uploaded_file, 'filename', ''):
        return save_manual_icon_upload(website, uploaded_file, sync_imagebed=sync_imagebed)

    if icon_url is not None:
        trimmed_icon_url = icon_url.strip()
        current_local_url = get_local_icon_url(meta)
        if meta.source_mode == 'manual_upload' and (
            not trimmed_icon_url or trimmed_icon_url == current_local_url
        ):
            if current_local_url:
                website.icon = current_local_url
                db.session.commit()
            if sync_imagebed is None:
                sync_imagebed = should_sync_imagebed(meta)
            if sync_imagebed and not get_imagebed_icon_url(meta):
                try:
                    upload_icon_to_imagebed(website)
                except Exception:
                    pass
            return get_website_icon_snapshot(website)

        if trimmed_icon_url:
            return mark_icon_manual_url(
                website,
                trimmed_icon_url,
                sync_local=sync_local,
                sync_imagebed=sync_imagebed,
            )

        website.icon = ''
        meta.source_mode = 'auto'
        meta.fetch_status = 'pending'
        meta.last_error = None
        db.session.commit()

    if auto_fetch or (meta.source_mode == 'auto' and not (website.icon or '').strip()):
        return refresh_icon_from_source(
            website,
            force=bool(auto_fetch or icon_url is not None),
            sync_local=sync_local,
            sync_imagebed=sync_imagebed,
        )

    if sync_local or sync_imagebed:
        return refresh_icon_from_source(
            website,
            force=False,
            sync_local=sync_local,
            sync_imagebed=sync_imagebed,
        )

    return get_website_icon_snapshot(website)


def create_icon_sync_task(
    task_type: str,
    scope_type: str = 'all',
    params: dict[str, Any] | None = None,
    created_by_id: int | None = None,
) -> IconSyncTask:
    task = IconSyncTask(
        task_type=task_type,
        scope_type=scope_type,
        params_json=json.dumps(params or {}, ensure_ascii=False),
        status='pending',
        created_by_id=created_by_id,
    )
    db.session.add(task)
    _commit_db_session()
    return task


def serialize_icon_sync_task(task: IconSyncTask | None) -> dict[str, Any]:
    if not task:
        return {
            'id': None,
            'task_type': None,
            'status': 'idle',
            'total': 0,
            'processed': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'error_summary': None,
            'started_at': None,
            'finished_at': None,
            'elapsed_time': '0s',
            'is_running': False,
            'percent': 0,
        }

    end_time = task.finished_at or _now()
    start_time = task.started_at or task.created_at
    elapsed = (end_time - start_time).total_seconds() if start_time else 0
    percent = int((task.processed / task.total) * 100) if task.total else 0

    return {
        'id': task.id,
        'task_type': task.task_type,
        'status': task.status,
        'total': task.total or 0,
        'processed': task.processed or 0,
        'success': task.success or 0,
        'failed': task.failed or 0,
        'skipped': task.skipped or 0,
        'error_summary': task.error_summary,
        'started_at': _iso(task.started_at),
        'finished_at': _iso(task.finished_at),
        'elapsed_time': _format_elapsed(elapsed),
        'is_running': task.status in {'pending', 'running'},
        'percent': percent,
    }


def recover_stale_icon_sync_tasks(grace_seconds: int = ICON_TASK_STALE_GRACE_SECONDS) -> int:
    now = _now()
    stale_tasks = (
        IconSyncTask.query.filter(IconSyncTask.status.in_(['pending', 'running']))
        .order_by(IconSyncTask.created_at.asc())
        .all()
    )

    recovered = 0
    for task in stale_tasks:
        if _is_registered_task_alive(task.id):
            continue

        reference_time = _task_reference_time(task)
        if reference_time and (now - reference_time).total_seconds() < grace_seconds:
            continue

        task.status = 'failed'
        task.finished_at = now
        if not task.error_summary:
            task.error_summary = '任务因应用重启或后台线程退出而中断，已自动标记为失败'
        recovered += 1
        _icon_task_threads.pop(task.id, None)

    if recovered:
        _commit_db_session()
    return recovered


def _load_task_websites(task: IconSyncTask) -> list[int]:
    params = _read_json(task.params_json or '{}') or {}
    query = Website.query
    if task.scope_type == 'selected':
        website_ids = params.get('website_ids') or []
        if website_ids:
            query = query.filter(Website.id.in_(website_ids))

    websites = query.order_by(Website.id.asc()).all()
    filtered: list[int] = []

    for website in websites:
        snapshot = get_website_icon_snapshot(website)
        if task.task_type == ICON_TASK_SYNC_MISSING:
            if not snapshot.get('url'):
                filtered.append(website.id)
        elif task.task_type == ICON_TASK_SYNC_LOCAL:
            if not snapshot.get('has_local') and (snapshot.get('source_url') or snapshot.get('has_imagebed')):
                filtered.append(website.id)
        elif task.task_type == ICON_TASK_SYNC_IMAGEBED:
            if not snapshot.get('has_imagebed') and (snapshot.get('has_local') or snapshot.get('source_url')):
                filtered.append(website.id)
        elif task.task_type == ICON_TASK_RETRY_FAILED:
            if 'failed' in {
                snapshot.get('fetch_status'),
                snapshot.get('local_status'),
                snapshot.get('imagebed_status'),
            }:
                filtered.append(website.id)
        elif task.task_type == ICON_TASK_REFRESH_SOURCE:
            if snapshot.get('source_mode') == 'auto':
                filtered.append(website.id)
        else:
            filtered.append(website.id)

    return filtered


def _run_task_for_website(task: IconSyncTask, website: Website) -> str:
    snapshot = get_website_icon_snapshot(website)

    if task.task_type == ICON_TASK_SYNC_MISSING:
        if snapshot.get('url'):
            return 'skipped'
        refreshed = refresh_icon_from_source(website, force=True)
        return 'success' if refreshed.get('url') else 'failed'

    if task.task_type == ICON_TASK_SYNC_LOCAL:
        if snapshot.get('has_local'):
            return 'skipped'
        source_url = snapshot.get('source_url') or snapshot.get('imagebed_url')
        if not source_url:
            return 'failed'
        downloaded = download_icon_to_local(
            website,
            source_url,
            update_legacy_icon=bool(snapshot.get('source_url') and source_url == snapshot.get('source_url')),
        )
        return 'success' if downloaded.get('has_local') else 'failed'

    if task.task_type == ICON_TASK_SYNC_IMAGEBED:
        if snapshot.get('has_imagebed'):
            return 'skipped'
        if not snapshot.get('has_local'):
            source_url = snapshot.get('source_url') or snapshot.get('imagebed_url')
            if not source_url:
                return 'failed'
            download_icon_to_local(website, source_url)
        uploaded = upload_icon_to_imagebed(website)
        return 'success' if uploaded.get('has_imagebed') else 'failed'

    if task.task_type == ICON_TASK_RETRY_FAILED:
        did_work = False
        latest_snapshot = snapshot

        if latest_snapshot.get('fetch_status') == 'failed':
            latest_snapshot = refresh_icon_from_source(website, force=True)
            did_work = True

        if latest_snapshot.get('local_status') == 'failed':
            source_url = latest_snapshot.get('source_url') or latest_snapshot.get('imagebed_url')
            if source_url:
                latest_snapshot = download_icon_to_local(
                    website,
                    source_url,
                    update_legacy_icon=bool(
                        latest_snapshot.get('source_url') and source_url == latest_snapshot.get('source_url')
                    ),
                )
                did_work = True

        if latest_snapshot.get('imagebed_status') == 'failed':
            if not latest_snapshot.get('has_local'):
                source_url = latest_snapshot.get('source_url') or latest_snapshot.get('imagebed_url')
                if source_url:
                    latest_snapshot = download_icon_to_local(
                        website,
                        source_url,
                        update_legacy_icon=bool(
                            latest_snapshot.get('source_url') and source_url == latest_snapshot.get('source_url')
                        ),
                    )
            latest_snapshot = upload_icon_to_imagebed(website)
            did_work = True

        if not did_work:
            return 'skipped'

        if 'failed' in {
            latest_snapshot.get('fetch_status'),
            latest_snapshot.get('local_status'),
            latest_snapshot.get('imagebed_status'),
        }:
            return 'failed'
        return 'success'

    if task.task_type == ICON_TASK_REFRESH_SOURCE:
        if snapshot.get('source_mode') != 'auto':
            return 'skipped'
        refreshed = refresh_icon_from_source(website, force=True, sync_local=False, sync_imagebed=False)
        return 'success' if refreshed.get('source_url') else 'failed'

    return 'skipped'


def _execute_icon_sync_task(app: Any, task_id: int) -> None:
    with app.app_context():
        task = IconSyncTask.query.get(task_id)
        if not task:
            _icon_task_threads.pop(task_id, None)
            db.session.remove()
            return

        task.status = 'running'
        task.started_at = _now()
        task.error_summary = None
        _commit_db_session()

        try:
            website_ids = _load_task_websites(task)
            task.total = len(website_ids)
            _commit_db_session()

            for website_id in website_ids:
                website = db.session.get(Website, website_id)
                if not website:
                    task.processed += 1
                    task.skipped += 1
                    _commit_db_session()
                    continue

                try:
                    result = _run_task_for_website(task, website)
                except Exception as exc:
                    meta = WebsiteIcon.query.filter_by(website_id=website_id).first()
                    if meta:
                        meta.last_error = str(exc)
                    _commit_db_session()
                    result = 'failed'
                    if not task.error_summary:
                        task.error_summary = f'website_id={website_id}: {exc}'

                task.processed += 1
                if result == 'success':
                    task.success += 1
                elif result == 'failed':
                    task.failed += 1
                else:
                    task.skipped += 1
                _commit_db_session()

            task.status = 'completed'
        except Exception as exc:
            db.session.rollback()
            task.status = 'failed'
            task.error_summary = str(exc)
        finally:
            task.finished_at = _now()
            try:
                _commit_db_session()
            finally:
                db.session.remove()
            _icon_task_threads.pop(task_id, None)


def start_icon_sync_task(
    task_type: str,
    scope_type: str = 'all',
    params: dict[str, Any] | None = None,
    created_by_id: int | None = None,
) -> tuple[IconSyncTask, bool]:
    recover_stale_icon_sync_tasks()
    active_task = (
        IconSyncTask.query.filter(IconSyncTask.status.in_(['pending', 'running']))
        .order_by(IconSyncTask.created_at.desc())
        .first()
    )
    if active_task:
        return active_task, False

    task = create_icon_sync_task(task_type, scope_type=scope_type, params=params, created_by_id=created_by_id)
    app = current_app._get_current_object()
    worker = threading.Thread(target=_execute_icon_sync_task, args=(app, task.id), daemon=True)
    _icon_task_threads[task.id] = worker
    worker.start()
    return task, True


def get_icon_dashboard_summary() -> dict[str, Any]:
    websites = Website.query.options(
        joinedload(Website.icon_meta).joinedload(WebsiteIcon.icon_asset)
    ).all()
    summary = {
        'total_websites': len(websites),
        'source_available': 0,
        'local_available': 0,
        'imagebed_available': 0,
        'manual_upload_count': 0,
        'shared_asset_count': 0,
        'fetch_failed': 0,
        'local_failed': 0,
        'imagebed_failed': 0,
        'display_missing': 0,
    }

    for website in websites:
        snapshot = get_website_icon_snapshot(website)
        if snapshot.get('has_source'):
            summary['source_available'] += 1
        if snapshot.get('has_local'):
            summary['local_available'] += 1
        if snapshot.get('has_imagebed'):
            summary['imagebed_available'] += 1
        if snapshot.get('source_mode') == 'manual_upload':
            summary['manual_upload_count'] += 1
        if snapshot.get('fetch_status') == 'failed':
            summary['fetch_failed'] += 1
        if snapshot.get('local_status') == 'failed':
            summary['local_failed'] += 1
        if snapshot.get('imagebed_status') == 'failed':
            summary['imagebed_failed'] += 1
        if not snapshot.get('url'):
            summary['display_missing'] += 1

    shared_asset_rows = (
        db.session.query(WebsiteIcon.icon_asset_id)
        .filter(WebsiteIcon.icon_asset_id.isnot(None))
        .group_by(WebsiteIcon.icon_asset_id)
        .having(func.count(WebsiteIcon.id) > 1)
        .all()
    )
    summary['shared_asset_count'] = len(shared_asset_rows)
    return summary


def delete_website_icon_assets(website_id: int, delete_record: bool = True) -> None:
    meta = WebsiteIcon.query.filter_by(website_id=website_id).first()
    if not meta:
        return

    asset = meta.icon_asset
    if delete_record:
        db.session.delete(meta)
    else:
        meta.icon_asset = None
    db.session.flush()

    _cleanup_orphan_asset(asset)
