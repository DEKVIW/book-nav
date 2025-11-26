#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""辅助工具函数"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse


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


def get_website_icon(url):
    """获取网站图标"""
    try:
        processed_url = url
        if not processed_url.startswith(('http://', 'https://')):
            processed_url = 'http://' + processed_url
        
        headers = {
            'User-Agent': 'xiaoxiaoapi/1.0.0 (https://xxapi.cn)'
        }
        
        api_url = f"https://v2.xxapi.cn/api/ico?url={processed_url}"
        response = requests.get(api_url, headers=headers, timeout=5)
        
        try:
            result = response.json()
            if result.get('code') == 200 and 'data' in result:
                return {
                    "success": True,
                    "icon_url": result['data']
                }
            else:
                error_msg = result.get('msg', '无法获取图标')
                parsed_url = urlparse(processed_url)
                domain = parsed_url.netloc
                return {
                    "success": False,
                    "message": error_msg,
                    "fallback_url": f"https://favicon.cccyun.cc/{domain}"
                }
        except ValueError:
            if response.status_code == 200 and response.text:
                icon_url = response.text.strip()
                if icon_url.startswith('http'):
                    return {
                        "success": True,
                        "icon_url": icon_url
                    }
        
        parsed_url = urlparse(processed_url)
        domain = parsed_url.netloc
        return {
            "success": False,
            "message": "无法解析API返回内容",
            "fallback_url": f"https://favicon.cccyun.cc/{domain}"
        }
    except Exception as e:
        try:
            parsed_url = urlparse(processed_url)
            domain = parsed_url.netloc
            return {
                "success": False,
                "message": str(e),
                "fallback_url": f"https://favicon.cccyun.cc/{domain}"
            }
        except:
            return {
                "success": False,
                "message": "URL解析失败",
                "fallback_url": None
            }

