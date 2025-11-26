from datetime import datetime
import random
import string
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app import db, login_manager
from config import Config

# 定义网站和标签的多对多关系表
website_tag = db.Table('website_tag',
    db.Column('website_id', db.Integer, db.ForeignKey('website.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tag.id'), primary_key=True)
)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True)
    email = db.Column(db.String(120), index=True, unique=True)
    password_hash = db.Column(db.String(128))
    avatar = db.Column(db.String(255))  # 用户头像字段
    is_admin = db.Column(db.Boolean, default=False)
    is_superadmin = db.Column(db.Boolean, default=False)  # 超级管理员标识
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    websites = db.relationship('Website', backref='creator', lazy='dynamic')
    
    def __repr__(self):
        return f'<User {self.username}>'
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(id):
    return User.query.get(int(id))


class InvitationCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(16), index=True, unique=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    used_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    used_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    used_by = db.relationship('User', foreign_keys=[used_by_id])
    
    @staticmethod
    def generate_code():
        length = Config.INVITATION_CODE_LENGTH
        chars = string.ascii_letters + string.digits
        while True:
            code = ''.join(random.choice(chars) for _ in range(length))
            if not InvitationCode.query.filter_by(code=code).first():
                return code
    
    def __repr__(self):
        return f'<InvitationCode {self.code}>'


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), index=True)
    description = db.Column(db.String(256))
    icon = db.Column(db.String(64))
    color = db.Column(db.String(16))
    order = db.Column(db.Integer, default=0)
    display_limit = db.Column(db.Integer, default=10)  # 首页展示数量限制，默认为10个
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 添加父分类关系
    parent_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    
    # 关系定义
    children = db.relationship('Category', 
                              backref=db.backref('parent', remote_side=[id]),
                              lazy='dynamic')
    websites = db.relationship('Website', backref='category', lazy='dynamic')
    
    def __repr__(self):
        return f'<Category {self.name}>'
        
    def get_ancestors(self):
        """获取所有祖先分类，从直接父级到顶级"""
        ancestors = []
        current = self.parent
        while current:
            ancestors.append(current)
            current = current.parent
        return ancestors[::-1]  # 逆序返回，从顶级到直接父级
    
    def is_descendant_of(self, category_id):
        """检查当前分类是否是指定分类的后代"""
        if self.parent_id is None:
            return False
        if self.parent_id == category_id:
            return True
        return self.parent.is_descendant_of(category_id)
    
    def get_all_descendants(self):
        """获取所有后代分类（递归）"""
        result = []
        for child in self.children:
            result.append(child)
            result.extend(child.get_all_descendants())
        return result


class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), index=True, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Tag {self.name}>'


class Website(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128))
    url = db.Column(db.String(256))
    description = db.Column(db.String(512))
    icon = db.Column(db.String(256))
    views = db.Column(db.Integer, default=0)
    is_featured = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sort_order = db.Column(db.Integer, default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    # 私有链接相关字段
    is_private = db.Column(db.Boolean, default=False)
    visible_to = db.Column(db.String(512), default='')  # 存储可见用户ID，用逗号分隔
    
    # 统计相关字段
    views_today = db.Column(db.Integer, default=0)
    last_view = db.Column(db.DateTime, nullable=True)
    
    # 死链检测相关字段
    is_valid = db.Column(db.Boolean, default=True)  # 链接是否有效
    last_check = db.Column(db.DateTime, nullable=True)  # 最后检测时间
    
    def __repr__(self):
        return f'<Website {self.title}>'
        
    def is_visible_to(self, user):
        """检查链接是否对指定用户可见"""
        # 如果不是私有链接，对所有人可见
        if not self.is_private:
            return True
            
        # 如果是私有链接
        if user is None:  # 未登录用户
            return False
            
        # 创建者和管理员可见
        if user.is_admin or user.id == self.created_by_id:
            return True
            
        # 检查是否在可见用户列表中
        if self.visible_to:
            visible_user_ids = [int(id) for id in self.visible_to.split(',') if id]
            return user.id in visible_user_ids
            
        return False 


class WebsiteVector(db.Model):
    """网站向量元数据表"""
    id = db.Column(db.Integer, primary_key=True)
    website_id = db.Column(db.Integer, db.ForeignKey('website.id'), unique=True, nullable=False)
    vector_status = db.Column(db.String(20), default='pending')  # pending, completed, failed
    embedding_model = db.Column(db.String(128), default='text-embedding-3-small')
    dimension = db.Column(db.Integer, default=1536)  # 向量维度
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关联关系
    website = db.relationship('Website', backref='vector_info', uselist=False)
    
    def __repr__(self):
        return f'<WebsiteVector {self.website_id} - {self.vector_status}>'


class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(128), default="炫酷导航")
    site_logo = db.Column(db.String(256), nullable=True)  # 存储logo图片URL
    site_favicon = db.Column(db.String(256), nullable=True)  # 存储网站图标URL
    site_subtitle = db.Column(db.String(256), nullable=True)
    site_keywords = db.Column(db.String(512), nullable=True)
    site_description = db.Column(db.String(1024), nullable=True)
    footer_content = db.Column(db.Text, nullable=True)  # 自定义页脚内容
    background_image = db.Column(db.String(512), nullable=True)  # 旧字段，保留以确保兼容性
    enable_background = db.Column(db.Boolean, default=False)  # 旧字段，保留以确保兼容性
    background_type = db.Column(db.String(32), default='none')  # 背景类型：none, image, gradient, color
    background_url = db.Column(db.String(512), nullable=True)  # 背景图片URL或颜色值
    
    # PC端背景设置
    pc_background_type = db.Column(db.String(32), default='none')  # PC端背景类型
    pc_background_url = db.Column(db.String(512), nullable=True)  # PC端背景URL
    
    # 移动端背景设置
    mobile_background_type = db.Column(db.String(32), default='none')  # 移动端背景类型
    mobile_background_url = db.Column(db.String(512), nullable=True)  # 移动端背景URL
    
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 过渡页设置
    enable_transition = db.Column(db.Boolean, default=False)  # 是否启用过渡页
    transition_time = db.Column(db.Integer, default=5)  # 访客停留时间（秒），设为0表示不显示过渡页直接跳转
    admin_transition_time = db.Column(db.Integer, default=3)  # 管理员停留时间（秒），设为0表示不显示过渡页直接跳转
    transition_ad1 = db.Column(db.Text, nullable=True)
    transition_ad2 = db.Column(db.Text, nullable=True)  # 过渡页广告位2
    transition_remember_choice = db.Column(db.Boolean, default=True)  # 是否允许用户选择不再显示
    transition_show_description = db.Column(db.Boolean, default=True)  # 是否显示网站描述
    transition_theme = db.Column(db.String(32), default='default')  # 过渡页主题
    transition_color = db.Column(db.String(32), default='#6e8efb')
    
    # 公告设置
    announcement_enabled = db.Column(db.Boolean, default=False)  # 是否启用公告
    announcement_title = db.Column(db.String(128), nullable=True)  # 公告标题
    announcement_content = db.Column(db.Text, nullable=True)  # 公告内容
    announcement_start = db.Column(db.DateTime, nullable=True)  # 公告开始时间
    announcement_end = db.Column(db.DateTime, nullable=True)  # 公告结束时间
    announcement_remember_days = db.Column(db.Integer, default=7)  # 不再提示的天数
    
    # AI 搜索设置
    ai_search_enabled = db.Column(db.Boolean, default=False)  # 是否启用AI搜索功能
    ai_api_base_url = db.Column(db.String(512), nullable=True)  # AI API基础URL
    ai_api_key = db.Column(db.String(512), nullable=True)  # AI API密钥（加密存储）
    ai_model_name = db.Column(db.String(128), nullable=True)  # AI模型名称
    ai_temperature = db.Column(db.Float, default=0.7)  # AI温度参数
    ai_max_tokens = db.Column(db.Integer, default=500)  # AI最大token数
    
    # 向量搜索设置（基于 Qdrant）
    vector_search_enabled = db.Column(db.Boolean, default=False)  # 是否启用向量搜索
    qdrant_url = db.Column(db.String(512), default='http://localhost:6333')  # Qdrant 服务地址
    embedding_model = db.Column(db.String(128), default='text-embedding-3-small')  # Embedding 模型
    vector_similarity_threshold = db.Column(db.Float, default=0.3)  # 向量相似度阈值
    vector_max_results = db.Column(db.Integer, default=50)  # 向量搜索最大结果数
    
    @staticmethod
    def get_default_qdrant_url():
        """
        获取默认Qdrant URL（自动检测Docker环境）
        
        Docker环境中，nav容器访问qdrant容器应使用服务名'qdrant'
        本地开发环境使用'localhost'
        """
        import os
        # 检测是否在Docker容器中运行
        # 方法1: 检查是否存在 /.dockerenv 文件（Docker标准检测方法）
        # 方法2: 检查环境变量 DOCKER_CONTAINER
        # 方法3: 检查 /proc/self/cgroup 是否包含docker
        is_docker = False
        try:
            if os.path.exists('/.dockerenv'):
                is_docker = True
            elif os.environ.get('DOCKER_CONTAINER') == 'true':
                is_docker = True
            elif os.path.exists('/proc/self/cgroup'):
                with open('/proc/self/cgroup', 'r') as f:
                    if 'docker' in f.read():
                        is_docker = True
        except Exception:
            pass  # 如果检测失败，默认使用localhost
        
        if is_docker:
            # Docker环境中，使用服务名访问（docker-compose.yml中定义的服务名）
            return 'http://qdrant:6333'
        else:
            # 本地开发环境
            return 'http://localhost:6333'
    
    # 单例模式：确保只有一条记录
    @classmethod
    def get_settings(cls):
        """获取站点设置（单例模式）"""
        settings = cls.query.first()
        if not settings:
            settings = cls()
            db.session.add(settings)
            db.session.commit()
        return settings
    
    def __repr__(self):
        return f'<SiteSettings {self.site_name}>'


class Background(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128))  # 背景名称
    url = db.Column(db.String(512))  # 背景URL
    type = db.Column(db.String(32))  # 背景类型：image, gradient, color
    device_type = db.Column(db.String(32))  # 设备类型：pc, mobile, both
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    created_by = db.relationship('User', backref='backgrounds')
    
    def __repr__(self):
        return f'<Background {self.title}>'


class OperationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    operation_type = db.Column(db.String(50))  # ADD, MODIFY, DELETE
    website_id = db.Column(db.Integer, nullable=True)  # 可以为空，表示记录已被删除的网站
    website_title = db.Column(db.String(128), nullable=True)
    website_url = db.Column(db.String(256), nullable=True)
    website_icon = db.Column(db.String(256), nullable=True)
    category_id = db.Column(db.Integer, nullable=True)
    category_name = db.Column(db.String(64), nullable=True)
    details = db.Column(db.Text, nullable=True)  # 存储更多操作细节，JSON格式
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='operations')
    
    def __repr__(self):
        return f'<OperationLog {self.operation_type} - {self.website_title}>'


class DeadlinkCheck(db.Model):
    """死链检测记录模型"""
    id = db.Column(db.Integer, primary_key=True)
    check_id = db.Column(db.String(36), index=True)  # 检测批次ID，使用UUID
    website_id = db.Column(db.Integer, db.ForeignKey('website.id'), nullable=False)
    url = db.Column(db.String(256), nullable=False)
    is_valid = db.Column(db.Boolean, default=True)  # True: 有效链接, False: 无效链接
    status_code = db.Column(db.Integer, nullable=True)  # HTTP状态码
    error_type = db.Column(db.String(50), nullable=True)  # 错误类型: timeout, connection_error, etc.
    error_message = db.Column(db.Text, nullable=True)  # 详细错误信息
    response_time = db.Column(db.Float, nullable=True)  # 响应时间(秒)
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 关系定义
    website = db.relationship('Website', backref='deadlink_checks')
    
    def __repr__(self):
        return f'<DeadlinkCheck {self.url} - {"Valid" if self.is_valid else "Invalid"}>'
