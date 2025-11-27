from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, TextAreaField, BooleanField, SubmitField, SelectField, HiddenField, IntegerField, PasswordField, DateTimeField
from wtforms.validators import DataRequired, Length, URL, Optional, ValidationError, Email, EqualTo, NumberRange
from app.models import Category, User
import re

class CategoryForm(FlaskForm):
    name = StringField('分类名称', validators=[DataRequired(), Length(max=64)])
    description = TextAreaField('分类描述', validators=[Length(max=256)])
    icon = StringField('图标', validators=[Length(max=64)])
    color = StringField('颜色', validators=[Length(max=16)])
    order = IntegerField('排序', default=0)
    display_limit = IntegerField('首页展示数量', default=10)
    parent_id = SelectField('父分类', coerce=int, validators=[Optional()])
    submit_btn = SubmitField('提交')
    
    def __init__(self, *args, **kwargs):
        super(CategoryForm, self).__init__(*args, **kwargs)
        self.parent_id.choices = [(0, '-- 无 --')] + [
            (c.id, c.name) for c in Category.query.filter(Category.parent_id.is_(None)).all()
        ]
        
    def validate_parent_id(self, field):
        if field.data == 0:
            field.data = None

class WebsiteForm(FlaskForm):
    """添加/编辑网站表单"""
    title = StringField('网站名称', validators=[DataRequired(), Length(max=128)])
    url = StringField('网站URL', validators=[DataRequired(), URL(), Length(max=256)])
    description = TextAreaField('网站描述', validators=[Optional(), Length(max=512)])
    icon = StringField('图标URL', validators=[Optional(), Length(max=256)])
    category_id = SelectField('分类', coerce=int, validators=[DataRequired()])
    sort_order = IntegerField('排序权重', validators=[Optional(), NumberRange(min=0, max=9999)], 
                            default=0, description='值越大排序越靠前，默认为0')
    is_featured = BooleanField('推荐')
    is_private = BooleanField('设为私有')
    submit_btn = SubmitField('提交')
    
    def __init__(self, *args, **kwargs):
        super(WebsiteForm, self).__init__(*args, **kwargs)
        self.category_id.choices = [(c.id, c.name) for c in Category.query.order_by(Category.order.asc()).all()]

class InvitationForm(FlaskForm):
    count = IntegerField('生成数量', default=1, validators=[DataRequired()])
    submit_btn = SubmitField('生成邀请码')

def validate_qdrant_url(form, field):
    """自定义 Qdrant URL 验证器，允许末尾斜杠和 Docker 服务名"""
    if field.data:
        url = field.data.strip()
        # 去除末尾斜杠
        url = url.rstrip('/')
        # 验证 URL 格式（允许 http:// 或 https://）
        # 支持：标准域名、localhost、IP 地址、Docker 服务名（如 qdrant）
        url_pattern = re.compile(
            r'^https?://'  # http:// 或 https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # 标准域名
            r'localhost|'  # localhost
            r'[A-Z0-9][A-Z0-9_-]*[A-Z0-9]|[A-Z0-9]|'  # Docker 服务名（字母数字下划线连字符，至少1个字符）
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP 地址
            r'(?::\d+)?'  # 可选端口
            r'(?:/?|[/?]\S+)?$', re.IGNORECASE)  # 路径（可选）
        
        if not url_pattern.match(url):
            raise ValidationError('请输入有效的 URL（例如: http://localhost:6333）')
        
        # 更新字段值为去除斜杠后的值
        field.data = url

class UserEditForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired(), Length(min=2, max=64)])
    email = StringField('电子邮箱', validators=[DataRequired(), Email(), Length(max=120)])
    password = StringField('密码 (留空则不修改)', validators=[Optional(), Length(min=8, max=150)])
    is_admin = BooleanField('管理员权限')
    is_superadmin = BooleanField('超级管理员权限')
    submit_btn = SubmitField('保存')
    
    def __init__(self, original_username, original_email, *args, **kwargs):
        super(UserEditForm, self).__init__(*args, **kwargs)
        self.original_username = original_username
        self.original_email = original_email
    
    def validate_username(self, username):
        if username.data != self.original_username:
            user = User.query.filter_by(username=username.data).first()
            if user is not None:
                raise ValidationError('该用户名已被使用')
    
    def validate_email(self, email):
        if email.data != self.original_email:
            user = User.query.filter_by(email=email.data).first()
            if user is not None:
                raise ValidationError('该邮箱已被使用')

class SiteSettingsForm(FlaskForm):
    site_name = StringField('站点名称', validators=[DataRequired(), Length(max=128)])
    site_subtitle = StringField('站点副标题', validators=[Optional(), Length(max=256)])
    site_logo = StringField('站点Logo URL', validators=[Optional(), Length(max=256)])
    logo_file = FileField('上传Logo', validators=[FileAllowed(['jpg', 'png', 'gif', 'svg'], '只允许上传图片!')])
    site_favicon = StringField('站点图标URL', validators=[Optional(), Length(max=256)])
    favicon_file = FileField('上传Favicon', validators=[FileAllowed(['ico', 'png', 'jpg', 'svg'], '只允许上传图片!')])
    site_keywords = StringField('站点关键词', validators=[Optional(), Length(max=512)])
    site_description = TextAreaField('站点描述', validators=[Optional(), Length(max=1024)])
    footer_content = TextAreaField('页脚内容', validators=[Optional()])
    background_type = SelectField('背景类型', choices=[
        ('none', '无背景'),
        ('image', '图片背景'),
        ('gradient', '渐变色背景'),
        ('color', '纯色背景')
    ], validators=[Optional()])
    background_url = StringField('背景URL', validators=[Optional(), Length(max=512)])
    background_file = FileField('上传背景图片', validators=[FileAllowed(['jpg', 'png', 'gif', 'webp'], '只允许上传图片!')])
    
    # 过渡页设置
    enable_transition = BooleanField('启用过渡页')
    transition_time = IntegerField('访客停留时间', validators=[NumberRange(min=0, max=30)], default=5, description='设置为0则不显示过渡页直接跳转')
    admin_transition_time = IntegerField('管理员停留时间', validators=[NumberRange(min=0, max=30)], default=3, description='设置为0则不显示过渡页直接跳转')
    transition_ad1 = TextAreaField('广告1', validators=[Optional()])
    transition_ad2 = TextAreaField('广告2', validators=[Optional()])
    transition_remember_choice = BooleanField('允许用户选择不再显示')
    transition_show_description = BooleanField('显示网站描述')
    transition_theme = SelectField('主题风格', choices=[
        ('default', '默认主题'),
        ('minimal', '极简主题'),
        ('card', '卡片主题'),
        ('dark', '暗色主题')
    ], validators=[Optional()])
    transition_color = StringField('主色调', validators=[Optional(), Length(max=32)])
    
    # PC端背景设置
    pc_background_type = SelectField('PC端背景类型', choices=[
        ('none', '无背景'),
        ('image', '图片背景'),
        ('gradient', '渐变色背景'),
        ('color', '纯色背景')
    ], validators=[Optional()])
    pc_background_url = StringField('PC端背景URL', validators=[Optional(), Length(max=512)])
    pc_background_file = FileField('上传PC端背景图片', validators=[FileAllowed(['jpg', 'png', 'gif', 'webp'], '只允许上传图片!')])

    # 移动端背景设置
    mobile_background_type = SelectField('移动端背景类型', choices=[
        ('none', '无背景'),
        ('image', '图片背景'),
        ('gradient', '渐变色背景'),
        ('color', '纯色背景')
    ], validators=[Optional()])
    mobile_background_url = StringField('移动端背景URL', validators=[Optional(), Length(max=512)])
    mobile_background_file = FileField('上传移动端背景图片', validators=[FileAllowed(['jpg', 'png', 'gif', 'webp'], '只允许上传图片!')])
    
    # 公告设置
    announcement_enabled = BooleanField('启用公告')
    announcement_title = StringField('公告标题', validators=[Optional(), Length(max=128)])
    announcement_content = TextAreaField('公告内容', validators=[Optional()])
    announcement_start = DateTimeField('开始时间', validators=[Optional()], format='%Y-%m-%dT%H:%M')
    announcement_end = DateTimeField('结束时间', validators=[Optional()], format='%Y-%m-%dT%H:%M')
    announcement_remember_days = IntegerField('不再提示天数', validators=[Optional(), NumberRange(min=1, max=365)], default=7)
    
    # AI 搜索设置
    ai_search_enabled = BooleanField('启用AI智能搜索')
    ai_search_allow_anonymous = BooleanField('允许非登录用户使用AI搜索')
    ai_api_base_url = StringField('API基础URL', validators=[Optional(), URL(), Length(max=512)], description='例如: https://api.openai.com')
    ai_api_key = PasswordField('API密钥', validators=[Optional(), Length(max=256)], description='留空则不修改现有密钥')
    ai_model_name = StringField('模型名称', validators=[Optional(), Length(max=128)], default='gpt-3.5-turbo', description='例如: gpt-3.5-turbo, gpt-4, claude-3-sonnet')
    ai_temperature = StringField('温度参数', validators=[Optional()], default='0.7', description='0-1之间，控制随机性，默认0.7')
    ai_max_tokens = IntegerField('最大Token数', validators=[Optional(), NumberRange(min=100, max=2000)], default=500)
    
    # 向量搜索设置（基于 Qdrant）
    vector_search_enabled = BooleanField('启用向量搜索', description='启用后使用 Qdrant 向量数据库进行高性能语义搜索')
    qdrant_url = StringField('Qdrant 地址', validators=[Optional(), validate_qdrant_url, Length(max=512)], default='http://localhost:6333', description='Qdrant 向量数据库服务地址')
    embedding_model = StringField('Embedding 模型', validators=[Optional(), Length(max=128)], default='text-embedding-3-small', description='用于生成向量的模型名称。常见模型：text-embedding-3-small, text-embedding-ada-002, bge-large-zh-v1.5 等。请根据你的 API 服务支持的模型填写。')
    vector_similarity_threshold = StringField('相似度阈值', validators=[Optional()], default='0.3', description='0-1之间，值越小结果越多，默认0.3')
    vector_max_results = IntegerField('最大结果数', validators=[Optional(), NumberRange(min=10, max=200)], default=50, description='向量搜索返回的最大结果数')
    
    submit_btn = SubmitField('保存设置')

class BackgroundForm(FlaskForm):
    title = StringField('背景名称', validators=[DataRequired(), Length(max=128)])
    url = StringField('背景URL', validators=[Optional(), Length(max=512)])
    type = SelectField('背景类型', choices=[
        ('image', '图片背景'),
        ('gradient', '渐变色背景'),
        ('color', '纯色背景')
    ], validators=[DataRequired()])
    device_type = SelectField('设备类型', choices=[
        ('pc', '电脑端'),
        ('mobile', '移动端'),
        ('both', '全部设备')
    ], validators=[DataRequired()])
    background_file = FileField('上传背景图片', validators=[FileAllowed(['jpg', 'png', 'gif', 'webp'], '只允许上传图片!')])
    submit_btn = SubmitField('保存背景')

class DataImportForm(FlaskForm):
    """数据导入表单"""
    db_file = FileField('数据库文件', validators=[
        DataRequired(),
        FileAllowed(['db', 'db3', 'sqlite', 'sqlite3'], '只允许上传SQLite数据库文件')
    ])
    import_type = SelectField('导入类型', choices=[
        ('merge', '合并 - 保留现有数据'),
        ('replace', '替换 - 清空现有数据')
    ])
    submit_btn = SubmitField('开始导入') 