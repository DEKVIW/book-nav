#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工具API路由"""

from flask import request, jsonify, Response, stream_with_context
from flask_login import current_user
from app.main import bp
from app.models import Website, Category
from app.main.utils import parse_website_info, get_website_icon
from urllib.parse import urlparse
import json
import requests
from bs4 import BeautifulSoup


@bp.route('/site/<int:site_id>/info')
def site_info(site_id):
    """获取网站信息"""
    try:
        site = Website.query.get_or_404(site_id)
        
        category_data = None
        if site.category:
            category_data = {
                'id': site.category.id,
                'name': site.category.name,
                'icon': site.category.icon,
                'color': site.category.color
            }
            
        website_data = {
            'id': site.id,
            'title': site.title,
            'url': site.url,
            'description': site.description,
            'icon': site.icon,
            'category': category_data,
            'views': site.views,
            'is_private': site.is_private
        }
        
        return jsonify({"success": True, "website": website_data})
    except Exception as e:
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@bp.route('/api/fetch_website_info')
def fetch_website_info():
    """获取网站信息（标题、描述、图标）"""
    url = request.args.get('url', '')
    if not url:
        return jsonify({"success": False, "message": "未提供URL参数"})
    
    result = parse_website_info(url)
    
    icon_result = get_website_icon(url)
    if icon_result["success"]:
        result["icon_url"] = icon_result["icon_url"]
    elif "fallback_url" in icon_result:
        result["icon_url"] = icon_result["fallback_url"]
    
    if result["success"]:
        processed_url = url
        if not processed_url.startswith(('http://', 'https://')):
            processed_url = 'https://' + processed_url
        parsed_url = urlparse(processed_url)
        domain = parsed_url.netloc
        result["domain"] = domain
        
    return jsonify(result)


@bp.route('/api/get_website_icon')
def api_get_website_icon():
    """获取网站图标的API接口"""
    url = request.args.get('url', '')
    if not url:
        return jsonify({"success": False, "message": "未提供URL参数"})
    
    result = get_website_icon(url)
    return jsonify(result)


@bp.route('/api/fetch_website_info_with_progress')
def fetch_website_info_with_progress():
    """获取网站信息的流式API（带进度）"""
    original_url = request.args.get('url', '')
    if not original_url:
        return jsonify({"success": False, "message": "未提供URL参数"})
    
    def generate():
        try:
            yield json.dumps({"stage": "init", "progress": 10, "message": "正在连接网站..."}) + "\n"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            }
            
            processed_url = original_url
            if not processed_url.startswith(('http://', 'https://')):
                processed_url = 'https://' + processed_url
                
            yield json.dumps({"stage": "connecting", "progress": 20, "message": "正在下载网页内容..."}) + "\n"
            response = requests.get(processed_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            yield json.dumps({"stage": "analyzing", "progress": 30, "message": "正在分析网页编码..."}) + "\n"
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
            
            yield json.dumps({"stage": "parsing", "progress": 40, "message": "正在解析网页内容..."}) + "\n"
            soup = BeautifulSoup(response.text, 'html.parser')
            
            yield json.dumps({"stage": "extracting_title", "progress": 50, "message": "正在提取网站标题..."}) + "\n"
            title = ""
            if soup.title:
                title = soup.title.string.strip() if soup.title.string else ""
            if not title:
                h1 = soup.find('h1')
                if h1:
                    title = h1.get_text().strip()
            
            yield json.dumps({"stage": "extracting_description", "progress": 60, "message": "正在提取网站描述..."}) + "\n"
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
            
            yield json.dumps({"stage": "extracting_icon", "progress": 70, "message": "正在获取网站图标..."}) + "\n"
            
            parsed_url = urlparse(processed_url)
            domain = parsed_url.netloc
            
            icon_url = f"https://favicon.cccyun.cc/{domain}"
            
            yield json.dumps({"stage": "fetching_icon", "progress": 80, "message": "正在获取高质量图标..."}) + "\n"
            try:
                icon_result = get_website_icon(processed_url)
                if icon_result["success"]:
                    icon_url = icon_result["icon_url"]
                elif "fallback_url" in icon_result:
                    icon_url = icon_result["fallback_url"]
            except Exception as e:
                pass
            
            yield json.dumps({
                "stage": "complete", 
                "progress": 100, 
                "message": "网站信息获取完成",
                "success": True,
                "title": title,
                "description": description,
                "domain": domain,
                "icon_url": icon_url
            }) + "\n"
            
        except Exception as e:
            error_message = str(e)
            yield json.dumps({
                "stage": "error",
                "progress": 0,
                "message": f"错误: {error_message}",
                "success": False,
                "title": "",
                "description": "",
                "domain": "",
                "icon_url": ""
            }) + "\n"
    
    return Response(stream_with_context(generate()), 
                   mimetype='text/event-stream',
                   headers={'Cache-Control': 'no-cache', 
                            'X-Accel-Buffering': 'no'})


@bp.route('/api/category/<int:category_id>/count')
def get_category_website_count(category_id):
    """获取分类下网站总数的API接口"""
    try:
        category = Category.query.get(category_id)
        if not category:
            return jsonify({
                'success': False,
                'message': '分类不存在'
            }), 404
        
        websites_query = Website.query.filter_by(category_id=category_id)
        
        if not current_user.is_authenticated:
            websites_query = websites_query.filter_by(is_private=False)
        elif not current_user.is_admin:
            websites_query = websites_query.filter(
                (Website.is_private == False) |
                (Website.created_by_id == current_user.id) |
                (Website.visible_to.contains(str(current_user.id)))
            )
        
        total_count = websites_query.count()
        
        return jsonify({
            'success': True,
            'category_id': category_id,
            'category_name': category.name,
            'total_count': total_count
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取网站总数失败: {str(e)}'
        }), 500

