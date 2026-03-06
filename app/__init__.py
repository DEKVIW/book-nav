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
    
    from app.models import User, InvitationCode, Category, Website, SiteSettings, WebDAVConfig, DeadlinkCheck
    
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
                'ai_search_allow_anonymous': False,
                'ai_api_base_url': None,
                'ai_api_key': None,
                'ai_model_name': None,
                'ai_temperature': 0.7,
                'ai_max_tokens': 500,
                # 向量搜索配置默认值
                'embedding_api_base_url': None,
                'embedding_api_key': None
            })
            return {'settings': default_settings}
    
    # 数据库和管理员初始化逻辑
    with app.app_context():
        db.create_all()
        # 数据库字段迁移（确保新字段自动添加）
        try:
            from app.utils.db_migration import migrate_site_settings_fields, migrate_webdav_config_table
            import os
            db_path = app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
            if db_path and os.path.exists(db_path):
                migrate_site_settings_fields(db_path)
                migrated = migrate_webdav_config_table(db_path)
                if migrated > 0:
                    print(f"已将旧 WebDAV 配置迁移到 webdav_config 表（{migrated} 条）")
        except Exception as e:
            # 迁移失败不影响应用启动
            print(f"数据库迁移警告: {str(e)}")
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
    
    # 启动 WebDAV 自动备份线程（仅在主进程中启动，避免 debug reloader 重复启动）
    try:
        import os as _os
        # 在 debug 模式下，只在 reloader 子进程中启动；非 debug 模式直接启动
        if not app.debug or _os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
            from app.admin.backups import start_auto_backup
            start_auto_backup(app)
    except Exception as e:
        print(f"自动备份线程启动警告: {str(e)}")
    
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