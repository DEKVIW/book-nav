from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from config import Config
import datetime
import json
from werkzeug.middleware.proxy_fix import ProxyFix
import sqlite3

# 设置SQLite允许多线程访问
sqlite3.threadsafety = 3  # 设置为最高等级的线程安全

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录以访问此页面'

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 配置SQLAlchemy以支持多线程
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {
            'check_same_thread': False  # 允许SQLite在多线程中使用
        }
    }
    
    # 应用 ProxyFix 中间件 (信任直接连接的 Nginx 代理)
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1
    )

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    
    from app.models import User, InvitationCode, Category, Website, SiteSettings, DeadlinkCheck
    
    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')
    
    from app.main import bp as main_bp
    app.register_blueprint(main_bp)
    
    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')
    
    from app.api import bp as api_bp
    app.register_blueprint(api_bp, url_prefix='/api')
    
    # 添加全局上下文处理器
    @app.context_processor
    def inject_now():
        return {'now': datetime.datetime.now()}
    
    @app.context_processor
    def inject_site_settings():
        try:
            settings = SiteSettings.get_settings()
            return {'settings': settings}
        except Exception as e:
            # 记录错误，但返回一个空的设置对象，避免模板渲染失败
            print(f"无法获取站点设置: {str(e)}")
            # 创建一个临时设置对象，包含基本默认值
            default_settings = type('DefaultSettings', (), {
                'site_name': '炫酷导航',
                'site_logo': None,
                'site_favicon': None,
                'site_subtitle': '',
                'site_keywords': '',
                'site_description': '',
                'footer_content': None,
                # AI搜索配置默认值
                'ai_search_enabled': False,
                'ai_api_base_url': None,
                'ai_api_key': None,
                'ai_model_name': None,
                'ai_temperature': 0.7,
                'ai_max_tokens': 500
            })
            return {'settings': default_settings}
    
    # 数据库和管理员初始化逻辑
    with app.app_context():
        db.create_all()
        # 管理员自动创建（合并邮箱冲突检测和升级逻辑）
        admin = User.query.filter_by(username=app.config['ADMIN_USERNAME']).first()
        admin_by_email = User.query.filter_by(email=app.config['ADMIN_EMAIL']).first()
        if not admin and not admin_by_email:
            admin = User(
                username=app.config['ADMIN_USERNAME'],
                email=app.config['ADMIN_EMAIL'],
                is_admin=True,
                is_superadmin=True
            )
            admin.set_password(app.config['ADMIN_PASSWORD'])
            db.session.add(admin)
            db.session.commit()
            print("默认管理员账户创建成功")
        elif admin_by_email and (not admin or admin.username != app.config['ADMIN_USERNAME']):
            print(f"已存在邮箱为 {app.config['ADMIN_EMAIL']} 的用户，跳过创建默认管理员")
        elif admin and not admin.is_superadmin:
            admin.is_superadmin = True
            db.session.commit()
            print("已将现有管理员升级为超级管理员")
        # 你原本 before_first_request 里的其他初始化逻辑可以放在这里
    
    # 注册模板过滤器
    @app.template_filter('from_json')
    def from_json(value):
        try:
            return json.loads(value) if value else {}
        except:
            return {}
    
    @app.template_filter('boolstr')
    def boolstr(value):
        return '是' if value else '否'
    
    return app

from app import models 