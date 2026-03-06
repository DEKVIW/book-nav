#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WebDAV 客户端工具类

使用 requests 库实现 WebDAV 协议操作，兼容主流 WebDAV 服务：
- 坚果云 (Jianguoyun)
- NextCloud / ownCloud
- Synology NAS
- QNAP NAS
- Box.com
- InfiniCLOUD (Teracloud)
- 其他标准 WebDAV 服务
"""

import os
import hashlib
import base64
import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urlparse
from datetime import datetime


# ==================== 密码加密工具 ====================

def encrypt_password(password, secret_key):
    """
    使用 SECRET_KEY 加密密码（PBKDF2 + XOR）
    
    Args:
        password: 明文密码
        secret_key: Flask SECRET_KEY
    
    Returns:
        加密后的 base64 字符串
    """
    if not password:
        return ''
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac('sha256', secret_key.encode('utf-8'), salt, 100000)
    password_bytes = password.encode('utf-8')
    key_stream = (key * (len(password_bytes) // len(key) + 1))[:len(password_bytes)]
    encrypted = bytes(a ^ b for a, b in zip(password_bytes, key_stream))
    return base64.b64encode(salt + encrypted).decode('utf-8')


def decrypt_password(encrypted_data, secret_key):
    """
    解密密码
    
    Args:
        encrypted_data: 加密后的 base64 字符串
        secret_key: Flask SECRET_KEY
    
    Returns:
        明文密码
    """
    if not encrypted_data:
        return ''
    try:
        raw = base64.b64decode(encrypted_data)
        if len(raw) < 17:  # 至少16字节salt + 1字节数据
            return encrypted_data  # 可能是未加密的旧数据
        salt = raw[:16]
        encrypted = raw[16:]
        key = hashlib.pbkdf2_hmac('sha256', secret_key.encode('utf-8'), salt, 100000)
        key_stream = (key * (len(encrypted) // len(key) + 1))[:len(encrypted)]
        decrypted = bytes(a ^ b for a, b in zip(encrypted, key_stream))
        return decrypted.decode('utf-8')
    except Exception:
        return encrypted_data  # 解密失败，返回原始值（兼容旧数据）


# ==================== WebDAV 客户端 ====================

class WebDAVClient:
    """
    WebDAV 客户端，使用 requests 实现，兼容主流 WebDAV 服务
    """
    
    # WebDAV XML 命名空间
    DAV_NS = 'DAV:'
    
    def __init__(self, url, username, password, verify_ssl=True, timeout=30):
        """
        初始化 WebDAV 客户端
        
        Args:
            url: WebDAV 服务器地址（如 https://dav.jianguoyun.com/dav/）
            username: 用户名
            password: 密码
            verify_ssl: 是否验证 SSL 证书
            timeout: 请求超时时间（秒）
        """
        self.base_url = url.rstrip('/')
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        
        # 创建 Session 复用连接
        self.session = requests.Session()
        self.session.verify = verify_ssl
        
        # 默认使用 Basic Auth（兼容性最好）
        self._auth = HTTPBasicAuth(username, password)
        self._auth_type = 'basic'
    
    def _build_url(self, path=''):
        """
        构建完整 URL，正确处理路径编码
        
        Args:
            path: 相对路径
        
        Returns:
            完整 URL 字符串
        """
        if not path or path == '/':
            return self.base_url + '/'
        
        # 清理路径
        path = path.strip('/')
        
        # 对路径中的每个部分进行 URL 编码（保留 /）
        encoded_parts = []
        for part in path.split('/'):
            encoded_parts.append(quote(part, safe=''))
        
        encoded_path = '/'.join(encoded_parts)
        return f"{self.base_url}/{encoded_path}"
    
    def _request(self, method, path='', **kwargs):
        """
        发送 WebDAV 请求，自动处理认证方式切换
        
        Args:
            method: HTTP 方法
            path: 相对路径
            **kwargs: 传递给 requests 的额外参数
        
        Returns:
            requests.Response 对象
        """
        url = self._build_url(path)
        kwargs.setdefault('timeout', self.timeout)
        kwargs.setdefault('auth', self._auth)
        
        response = self.session.request(method, url, **kwargs)
        
        # 如果 Basic Auth 返回 401，尝试 Digest Auth
        if response.status_code == 401 and self._auth_type == 'basic':
            self._auth = HTTPDigestAuth(self.username, self.password)
            self._auth_type = 'digest'
            kwargs['auth'] = self._auth
            response = self.session.request(method, url, **kwargs)
        
        return response
    
    def test_connection(self):
        """
        测试 WebDAV 连接
        
        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            # 方法1：尝试 PROPFIND（最可靠的 WebDAV 检测方式）
            headers = {'Depth': '0', 'Content-Type': 'application/xml; charset=utf-8'}
            body = '<?xml version="1.0" encoding="utf-8"?><propfind xmlns="DAV:"><prop><resourcetype/></prop></propfind>'
            
            response = self._request('PROPFIND', '', headers=headers, data=body.encode('utf-8'))
            
            if response.status_code == 207:  # Multi-Status
                return True, "WebDAV 连接成功"
            elif response.status_code == 401:
                return False, "认证失败，请检查用户名和密码"
            elif response.status_code == 403:
                return False, "访问被拒绝，请检查权限设置"
            elif response.status_code == 404:
                return False, "路径不存在，请检查 WebDAV 地址"
            elif response.status_code == 405:
                # 某些服务器不支持 PROPFIND，尝试 OPTIONS
                response2 = self._request('OPTIONS', '')
                if response2.status_code == 200:
                    dav_header = response2.headers.get('DAV', '')
                    if dav_header:
                        return True, "WebDAV 连接成功"
                    return True, "连接成功（服务器可能部分支持 WebDAV）"
                return False, f"服务器不支持 WebDAV（状态码: {response.status_code}）"
            else:
                return False, f"连接异常，HTTP 状态码: {response.status_code}"
                
        except requests.exceptions.SSLError:
            return False, "SSL 证书验证失败。如果是自签名证书，请在设置中关闭 SSL 验证"
        except requests.exceptions.ConnectionError:
            return False, "无法连接到服务器，请检查地址是否正确以及网络连接"
        except requests.exceptions.Timeout:
            return False, f"连接超时（{self.timeout}秒），请检查网络或服务器状态"
        except Exception as e:
            return False, f"连接错误: {str(e)}"
    
    def ensure_directory(self, path):
        """
        确保远端目录存在，不存在则逐级创建
        
        Args:
            path: 目录路径
        
        Returns:
            tuple: (success: bool, message: str)
        """
        if not path or path == '/':
            return True, "根目录已存在"
        
        path = path.strip('/')
        parts = path.split('/')
        current_path = ''
        
        for part in parts:
            current_path = f"{current_path}/{part}" if current_path else part
            
            # 检查目录是否存在（PROPFIND）
            headers = {'Depth': '0'}
            response = self._request('PROPFIND', current_path + '/', headers=headers)
            
            if response.status_code == 207:
                continue  # 目录已存在
            elif response.status_code == 404:
                # 目录不存在，创建
                response = self._request('MKCOL', current_path + '/')
                if response.status_code not in (201, 301):
                    return False, f"创建目录 '{current_path}' 失败（状态码: {response.status_code}）"
            elif response.status_code == 401:
                return False, "认证失败"
            # 其他状态码继续尝试（某些服务器 PROPFIND 返回非标准状态码）
        
        return True, "目录已就绪"
    
    def upload_file(self, remote_path, local_path):
        """
        上传文件到 WebDAV 服务器
        
        Args:
            remote_path: 远端文件路径
            local_path: 本地文件路径
        
        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            if not os.path.exists(local_path):
                return False, f"本地文件不存在: {local_path}"
            
            file_size = os.path.getsize(local_path)
            
            # 确保目标目录存在
            dir_path = '/'.join(remote_path.strip('/').split('/')[:-1])
            if dir_path:
                success, msg = self.ensure_directory(dir_path)
                if not success:
                    return False, f"创建远端目录失败: {msg}"
            
            # 上传文件
            with open(local_path, 'rb') as f:
                headers = {'Content-Type': 'application/octet-stream'}
                response = self._request(
                    'PUT',
                    remote_path,
                    headers=headers,
                    data=f,
                    timeout=max(self.timeout, 120)  # 上传使用较长超时
                )
            
            if response.status_code in (200, 201, 204):
                size_display = self._format_size(file_size)
                return True, f"上传成功（{size_display}）"
            elif response.status_code == 401:
                return False, "认证失败，请检查用户名和密码"
            elif response.status_code == 403:
                return False, "上传被拒绝，请检查写入权限"
            elif response.status_code == 507:
                return False, "服务器存储空间不足"
            elif response.status_code == 409:
                return False, "路径冲突，请检查远端目录是否存在"
            else:
                return False, f"上传失败，HTTP 状态码: {response.status_code}"
                
        except requests.exceptions.Timeout:
            return False, "上传超时，文件可能过大或网络较慢"
        except Exception as e:
            return False, f"上传错误: {str(e)}"
    
    def list_files(self, path='/'):
        """
        列出远端目录中的文件
        
        Args:
            path: 目录路径
        
        Returns:
            tuple: (success: bool, files: list | message: str)
            files 格式: [{'name': str, 'size': int, 'modified': str, 'is_dir': bool}, ...]
        """
        try:
            headers = {
                'Depth': '1',
                'Content-Type': 'application/xml; charset=utf-8'
            }
            body = '''<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:">
  <prop>
    <getlastmodified/>
    <getcontentlength/>
    <resourcetype/>
    <getcontenttype/>
  </prop>
</propfind>'''
            
            # 确保路径以 / 结尾
            if not path.endswith('/'):
                path = path + '/'
            
            response = self._request('PROPFIND', path, headers=headers, data=body.encode('utf-8'))
            
            if response.status_code == 207:
                files = self._parse_propfind_response(response.text, path)
                return True, files
            elif response.status_code == 404:
                return True, []  # 目录不存在，返回空列表
            elif response.status_code == 401:
                return False, "认证失败"
            else:
                return False, f"列表获取失败，HTTP 状态码: {response.status_code}"
                
        except Exception as e:
            return False, f"列表获取错误: {str(e)}"
    
    def download_file(self, remote_path, local_path):
        """
        从 WebDAV 服务器下载文件
        
        Args:
            remote_path: 远端文件路径
            local_path: 本地保存路径
        
        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            # 确保本地目录存在
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)
            
            response = self._request(
                'GET',
                remote_path,
                stream=True,
                timeout=max(self.timeout, 120)
            )
            
            if response.status_code == 200:
                with open(local_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                file_size = os.path.getsize(local_path)
                size_display = self._format_size(file_size)
                return True, f"下载成功（{size_display}）"
            elif response.status_code == 404:
                return False, "远端文件不存在"
            elif response.status_code == 401:
                return False, "认证失败"
            else:
                return False, f"下载失败，HTTP 状态码: {response.status_code}"
                
        except requests.exceptions.Timeout:
            return False, "下载超时"
        except Exception as e:
            # 清理可能的不完整文件
            if os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except OSError:
                    pass
            return False, f"下载错误: {str(e)}"
    
    def delete_file(self, remote_path):
        """
        删除远端文件
        
        Args:
            remote_path: 远端文件路径
        
        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            response = self._request('DELETE', remote_path)
            
            if response.status_code in (200, 204):
                return True, "删除成功"
            elif response.status_code == 404:
                return True, "文件已不存在"  # 幂等删除
            elif response.status_code == 401:
                return False, "认证失败"
            elif response.status_code == 403:
                return False, "删除被拒绝，请检查权限"
            else:
                return False, f"删除失败，HTTP 状态码: {response.status_code}"
                
        except Exception as e:
            return False, f"删除错误: {str(e)}"
    
    def _parse_propfind_response(self, xml_text, base_path):
        """
        解析 PROPFIND 的 XML 响应
        
        Args:
            xml_text: XML 响应文本
            base_path: 请求的基础路径
        
        Returns:
            list: 文件列表
        """
        files = []
        
        try:
            # 移除可能的 BOM
            if xml_text.startswith('\ufeff'):
                xml_text = xml_text[1:]
            
            root = ET.fromstring(xml_text)
            
            # 查找所有 response 元素（兼容多种命名空间写法）
            ns = {'d': self.DAV_NS}
            responses = root.findall('.//d:response', ns)
            
            if not responses:
                # 尝试无命名空间
                responses = root.findall('.//{DAV:}response')
            
            # 获取基础 URL 路径用于过滤当前目录
            parsed_base = urlparse(self.base_url)
            base_url_path = parsed_base.path.rstrip('/')
            
            for resp in responses:
                href = resp.find('d:href', ns)
                if href is None:
                    href = resp.find('{DAV:}href')
                if href is None:
                    continue
                
                href_text = unquote(href.text or '')
                
                # 判断是否为目录本身（跳过）
                clean_href = href_text.rstrip('/')
                
                # 构建期望的基础路径
                base_check = base_path.strip('/')
                if base_url_path:
                    full_base = f"{base_url_path}/{base_check}".rstrip('/')
                else:
                    full_base = f"/{base_check}".rstrip('/')
                
                if clean_href == full_base or clean_href == base_url_path:
                    continue  # 跳过目录本身
                
                # 获取属性
                propstat = resp.find('d:propstat', ns)
                if propstat is None:
                    propstat = resp.find('{DAV:}propstat')
                if propstat is None:
                    continue
                
                prop = propstat.find('d:prop', ns)
                if prop is None:
                    prop = propstat.find('{DAV:}prop')
                if prop is None:
                    continue
                
                # 判断是否为目录
                resource_type = prop.find('d:resourcetype', ns)
                if resource_type is None:
                    resource_type = prop.find('{DAV:}resourcetype')
                
                is_dir = False
                if resource_type is not None:
                    collection = resource_type.find('d:collection', ns)
                    if collection is None:
                        collection = resource_type.find('{DAV:}collection')
                    is_dir = collection is not None
                
                if is_dir:
                    continue  # 跳过子目录
                
                # 文件名
                name = href_text.rstrip('/').split('/')[-1]
                if not name:
                    continue
                
                # 文件大小
                content_length = prop.find('d:getcontentlength', ns)
                if content_length is None:
                    content_length = prop.find('{DAV:}getcontentlength')
                size = int(content_length.text) if content_length is not None and content_length.text else 0
                
                # 修改时间
                last_modified = prop.find('d:getlastmodified', ns)
                if last_modified is None:
                    last_modified = prop.find('{DAV:}getlastmodified')
                modified = last_modified.text if last_modified is not None else ''
                
                # 解析时间
                modified_display = self._parse_http_date(modified) if modified else ''
                
                files.append({
                    'name': name,
                    'size': size,
                    'size_display': self._format_size(size),
                    'modified': modified,
                    'modified_display': modified_display,
                    'is_dir': is_dir
                })
            
        except ET.ParseError:
            pass  # XML 解析失败，返回空列表
        except Exception:
            pass
        
        # 按修改时间降序排序
        files.sort(key=lambda x: x.get('modified', ''), reverse=True)
        
        return files
    
    @staticmethod
    def _parse_http_date(date_str):
        """解析 HTTP 日期格式为可读字符串"""
        if not date_str:
            return ''
        
        # 常见的 HTTP 日期格式
        formats = [
            '%a, %d %b %Y %H:%M:%S %Z',      # RFC 1123
            '%A, %d-%b-%y %H:%M:%S %Z',        # RFC 850
            '%a %b %d %H:%M:%S %Y',            # asctime
            '%Y-%m-%dT%H:%M:%S%z',             # ISO 8601
            '%Y-%m-%dT%H:%M:%SZ',              # ISO 8601 UTC
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
        
        return date_str  # 无法解析则返回原始值
    
    @staticmethod
    def _format_size(size_bytes):
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes}B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f}KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"

