from datetime import datetime
import json
import random
import string
from flask import current_app, has_app_context
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app import db, login_manager
from config import Config
from sqlalchemy.orm import backref
from sqlalchemy.exc import OperationalError

# 瀹氫箟缃戠珯鍜屾爣绛剧殑澶氬澶氬叧绯昏〃
website_tag = db.Table('website_tag',
    db.Column('website_id', db.Integer, db.ForeignKey('website.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tag.id'), primary_key=True)
)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True)
    email = db.Column(db.String(120), index=True, unique=True)
    password_hash = db.Column(db.String(128))
    avatar = db.Column(db.String(255))  # 鐢ㄦ埛澶村儚瀛楁
    is_admin = db.Column(db.Boolean, default=False)
    is_superadmin = db.Column(db.Boolean, default=False)  # 瓒呯骇绠＄悊鍛樻爣璇?
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
    display_limit = db.Column(db.Integer, default=10)  # 棣栭〉灞曠ず鏁伴噺闄愬埗锛岄粯璁や负10涓?
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 娣诲姞鐖跺垎绫诲叧绯?
    parent_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    
    # 鍏崇郴瀹氫箟
    children = db.relationship('Category', 
                              backref=db.backref('parent', remote_side=[id]),
                              lazy='dynamic')
    websites = db.relationship('Website', backref='category', lazy='dynamic')
    
    def __repr__(self):
        return f'<Category {self.name}>'
        
    def get_ancestors(self):
        """鑾峰彇鎵€鏈夌鍏堝垎绫伙紝浠庣洿鎺ョ埗绾у埌椤剁骇"""
        ancestors = []
        current = self.parent
        while current:
            ancestors.append(current)
            current = current.parent
        return ancestors[::-1]  # 閫嗗簭杩斿洖锛屼粠椤剁骇鍒扮洿鎺ョ埗绾?
    
    def is_descendant_of(self, category_id):
        """Check whether the current category is a descendant of the given category."""
        if self.parent_id is None:
            return False
        if self.parent_id == category_id:
            return True
        return self.parent.is_descendant_of(category_id)
    
    def get_all_descendants(self):
        """Return all descendant categories recursively."""
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
    
    # 绉佹湁閾炬帴鐩稿叧瀛楁
    is_private = db.Column(db.Boolean, default=False)
    visible_to = db.Column(db.String(512), default='')  # 瀛樺偍鍙鐢ㄦ埛ID锛岀敤閫楀彿鍒嗛殧
    
    # 缁熻鐩稿叧瀛楁
    views_today = db.Column(db.Integer, default=0)
    last_view = db.Column(db.DateTime, nullable=True)
    
    # 姝婚摼妫€娴嬬浉鍏冲瓧娈?
    is_valid = db.Column(db.Boolean, default=True)  # 閾炬帴鏄惁鏈夋晥
    last_check = db.Column(db.DateTime, nullable=True)  # 鏈€鍚庢娴嬫椂闂?

    @property
    def display_icon_url(self):
        from app.utils.icon_service import resolve_display_icon_url
        return resolve_display_icon_url(self)

    @property
    def display_icon_info(self):
        from app.utils.icon_service import get_website_icon_snapshot
        return get_website_icon_snapshot(self)
    
    def __repr__(self):
        return f'<Website {self.title}>'
        
    def is_visible_to(self, user):
        """妫€鏌ラ摼鎺ユ槸鍚﹀鎸囧畾鐢ㄦ埛鍙"""
        # 濡傛灉涓嶆槸绉佹湁閾炬帴锛屽鎵€鏈変汉鍙
        if not self.is_private:
            return True
            
        # 濡傛灉鏄鏈夐摼鎺?
        if user is None:  # 鏈櫥褰曠敤鎴?
            return False
            
        # 鍒涘缓鑰呭拰绠＄悊鍛樺彲瑙?
        if user.is_admin or user.id == self.created_by_id:
            return True
            
        # 妫€鏌ユ槸鍚﹀湪鍙鐢ㄦ埛鍒楄〃涓?
        if self.visible_to:
            visible_user_ids = [int(id) for id in self.visible_to.split(',') if id]
            return user.id in visible_user_ids
            
        return False 


class IconAsset(db.Model):
    __tablename__ = 'icon_asset'

    id = db.Column(db.Integer, primary_key=True)
    domain_key = db.Column(db.String(255), index=True)
    file_hash = db.Column(db.String(64), unique=True, index=True)
    source_url = db.Column(db.String(512), index=True)
    source_host = db.Column(db.String(255), index=True)
    local_path = db.Column(db.String(512))
    mime_type = db.Column(db.String(128))
    imagebed_provider = db.Column(db.String(64))
    imagebed_url = db.Column(db.String(1024))
    imagebed_delete_url = db.Column(db.String(1024))
    imagebed_payload_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<IconAsset {self.id} {self.file_hash}>'


class WebsiteIcon(db.Model):
    __tablename__ = 'website_icon'

    id = db.Column(db.Integer, primary_key=True)
    website_id = db.Column(db.Integer, db.ForeignKey('website.id'), unique=True, nullable=False, index=True)
    icon_asset_id = db.Column(db.Integer, db.ForeignKey('icon_asset.id'))
    domain_key = db.Column(db.String(255), index=True)
    source_mode = db.Column(db.String(32), default='auto')
    source_provider_override = db.Column(db.String(64), default='inherit')
    display_mode_override = db.Column(db.String(32), default='inherit')
    sync_local_mode = db.Column(db.String(32), default='inherit')
    sync_imagebed_mode = db.Column(db.String(32), default='inherit')
    fetch_status = db.Column(db.String(32), default='pending')
    local_status = db.Column(db.String(32), default='pending')
    imagebed_status = db.Column(db.String(32), default='pending')
    last_fetch_at = db.Column(db.DateTime, nullable=True)
    last_local_sync_at = db.Column(db.DateTime, nullable=True)
    last_imagebed_sync_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    website = db.relationship(
        'Website',
        backref=backref('icon_meta', uselist=False, cascade='all, delete-orphan'),
        uselist=False
    )
    icon_asset = db.relationship('IconAsset', backref=backref('website_icons', lazy='dynamic'))

    def __repr__(self):
        return f'<WebsiteIcon website={self.website_id} mode={self.source_mode}>'


class IconSyncTask(db.Model):
    __tablename__ = 'icon_sync_task'

    id = db.Column(db.Integer, primary_key=True)
    task_type = db.Column(db.String(64), nullable=False)
    scope_type = db.Column(db.String(32), default='all')
    params_json = db.Column(db.Text)
    status = db.Column(db.String(32), default='pending')
    total = db.Column(db.Integer, default=0)
    processed = db.Column(db.Integer, default=0)
    success = db.Column(db.Integer, default=0)
    failed = db.Column(db.Integer, default=0)
    skipped = db.Column(db.Integer, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    error_summary = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship('User', backref='icon_sync_tasks')

    def __repr__(self):
        return f'<IconSyncTask {self.task_type} {self.status}>'


class WebsiteVector(db.Model):
    """缃戠珯鍚戦噺鍏冩暟鎹〃"""
    id = db.Column(db.Integer, primary_key=True)
    website_id = db.Column(db.Integer, db.ForeignKey('website.id'), unique=True, nullable=False)
    vector_status = db.Column(db.String(20), default='pending')  # pending, completed, failed
    embedding_model = db.Column(db.String(128), default='text-embedding-3-small')
    dimension = db.Column(db.Integer, default=1536)  # 鍚戦噺缁村害
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 鍏宠仈鍏崇郴
    website = db.relationship('Website', backref='vector_info', uselist=False)
    
    def __repr__(self):
        return f'<WebsiteVector {self.website_id} - {self.vector_status}>'


class AIProviderConfig(db.Model):
    __tablename__ = 'ai_provider_config'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, default='Default AI Provider')
    api_base_url = db.Column(db.String(512), nullable=True)
    api_key = db.Column(db.String(512), nullable=True)
    interface_mode = db.Column(db.String(32), default='auto')
    enabled = db.Column(db.Boolean, default=True)
    priority = db.Column(db.Integer, default=100)
    model_catalog_json = db.Column(db.Text, nullable=True)
    recommended_models_json = db.Column(db.Text, nullable=True)
    probe_last_at = db.Column(db.DateTime, nullable=True)
    probe_error = db.Column(db.Text, nullable=True)
    probe_signature = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    @classmethod
    def ordered_query(cls, enabled_only=False):
        query = cls.query.order_by(cls.priority.asc(), cls.id.asc())
        if enabled_only:
            query = query.filter_by(enabled=True)
        return query

    def get_model_catalog(self):
        if not self.model_catalog_json:
            return []

        try:
            payload = json.loads(self.model_catalog_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get('models'), list):
            return payload['models']
        return []

    def set_model_catalog(self, models):
        self.model_catalog_json = json.dumps(models or [], ensure_ascii=False)

    def get_recommended_models(self):
        defaults = {
            'intent': '',
            'rerank': '',
            'translate': '',
            'site_info': '',
            'fallback': '',
        }
        if not self.recommended_models_json:
            return defaults

        try:
            payload = json.loads(self.recommended_models_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return defaults

        if not isinstance(payload, dict):
            return defaults

        result = defaults.copy()
        for key in result.keys():
            value = payload.get(key)
            result[key] = value.strip() if isinstance(value, str) else ''
        return result

    def set_recommended_models(self, models):
        payload = {}
        for key in ('intent', 'rerank', 'translate', 'site_info', 'fallback'):
            value = ''
            if isinstance(models, dict):
                raw_value = models.get(key)
                value = raw_value.strip() if isinstance(raw_value, str) else ''
            payload[key] = value
        self.recommended_models_json = json.dumps(payload, ensure_ascii=False)

    def clear_probe_data(self):
        self.model_catalog_json = None
        self.recommended_models_json = None
        self.probe_last_at = None
        self.probe_error = None
        self.probe_signature = None

    def masked_api_key(self):
        api_key = (self.api_key or '').strip()
        if not api_key:
            return ''
        if len(api_key) <= 8:
            return '*' * len(api_key)
        return api_key[:4] + ('*' * (len(api_key) - 8)) + api_key[-4:]

    def get_probe_stats(self):
        from app.utils.ai_model_discovery import summarize_probe_catalog

        return summarize_probe_catalog(self.get_model_catalog())

    def to_dict(self, include_catalog=True):
        data = {
            'id': self.id,
            'name': self.name or '',
            'api_base_url': self.api_base_url or '',
            'api_key_masked': self.masked_api_key(),
            'has_api_key': bool((self.api_key or '').strip()),
            'interface_mode': (self.interface_mode or 'auto').strip().lower() or 'auto',
            'enabled': bool(self.enabled),
            'priority': int(self.priority or 100),
            'recommended_models': self.get_recommended_models(),
            'probe_last_at': self.probe_last_at.isoformat() if self.probe_last_at else '',
            'probe_error': self.probe_error or '',
            'probe_signature': self.probe_signature or '',
            'stats': self.get_probe_stats(),
        }
        if include_catalog:
            data['catalog'] = self.get_model_catalog()
        return data

    def __repr__(self):
        return f'<AIProviderConfig {self.id} {self.name}>'


class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(128), default='酷站导航')
    site_logo = db.Column(db.String(256), nullable=True)
    site_favicon = db.Column(db.String(256), nullable=True)
    site_subtitle = db.Column(db.String(256), nullable=True)
    site_keywords = db.Column(db.String(512), nullable=True)
    site_description = db.Column(db.String(1024), nullable=True)
    footer_content = db.Column(db.Text, nullable=True)
    background_image = db.Column(db.String(512), nullable=True)
    enable_background = db.Column(db.Boolean, default=False)
    background_type = db.Column(db.String(32), default='none')
    background_url = db.Column(db.String(512), nullable=True)
    pc_background_type = db.Column(db.String(32), default='none')
    pc_background_url = db.Column(db.String(512), nullable=True)
    mobile_background_type = db.Column(db.String(32), default='none')
    mobile_background_url = db.Column(db.String(512), nullable=True)
    icon_display_mode = db.Column(db.String(32), default='smart')
    icon_auto_fetch_on_create = db.Column(db.Boolean, default=False)
    icon_default_sync_local = db.Column(db.Boolean, default=False)
    icon_default_sync_imagebed = db.Column(db.Boolean, default=False)
    icon_source_providers_json = db.Column(db.Text, nullable=True)
    icon_imagebed_provider = db.Column(db.String(64), nullable=True)
    icon_imagebed_api_url = db.Column(db.String(512), nullable=True)
    icon_imagebed_token = db.Column(db.String(512), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    enable_transition = db.Column(db.Boolean, default=False)
    transition_time = db.Column(db.Integer, default=5)
    admin_transition_time = db.Column(db.Integer, default=3)
    transition_ad1 = db.Column(db.Text, nullable=True)
    transition_ad2 = db.Column(db.Text, nullable=True)
    transition_remember_choice = db.Column(db.Boolean, default=True)
    transition_show_description = db.Column(db.Boolean, default=True)
    transition_theme = db.Column(db.String(32), default='default')
    transition_color = db.Column(db.String(32), default='#6e8efb')
    announcement_enabled = db.Column(db.Boolean, default=False)
    announcement_title = db.Column(db.String(128), nullable=True)
    announcement_content = db.Column(db.Text, nullable=True)
    announcement_start = db.Column(db.DateTime, nullable=True)
    announcement_end = db.Column(db.DateTime, nullable=True)
    announcement_remember_days = db.Column(db.Integer, default=7)
    ai_search_enabled = db.Column(db.Boolean, default=False)
    ai_search_allow_anonymous = db.Column(db.Boolean, default=False)
    ai_api_base_url = db.Column(db.String(512), nullable=True)
    ai_api_key = db.Column(db.String(512), nullable=True)
    ai_model_name = db.Column(db.String(128), nullable=True)
    ai_interface_mode = db.Column(db.String(32), default='auto')
    ai_temperature = db.Column(db.Float, default=0.7)
    ai_max_tokens = db.Column(db.Integer, default=500)
    ai_auto_model_selection_enabled = db.Column(db.Boolean, default=True)
    ai_model_catalog_json = db.Column(db.Text, nullable=True)
    ai_selected_intent_model = db.Column(db.String(128), nullable=True)
    ai_selected_rerank_model = db.Column(db.String(128), nullable=True)
    ai_selected_translate_model = db.Column(db.String(128), nullable=True)
    ai_selected_site_info_model = db.Column(db.String(128), nullable=True)
    ai_selected_fallback_model = db.Column(db.String(128), nullable=True)
    ai_model_probe_last_at = db.Column(db.DateTime, nullable=True)
    ai_model_probe_error = db.Column(db.Text, nullable=True)
    ai_model_probe_signature = db.Column(db.String(64), nullable=True)
    ai_task_bindings_json = db.Column(db.Text, nullable=True)
    ai_task_test_results_json = db.Column(db.Text, nullable=True)
    vector_search_enabled = db.Column(db.Boolean, default=False)
    qdrant_url = db.Column(db.String(512), default='http://localhost:6333')
    embedding_model = db.Column(db.String(128), default='text-embedding-3-small')
    embedding_api_base_url = db.Column(db.String(512), nullable=True)
    embedding_api_key = db.Column(db.String(512), nullable=True)
    vector_similarity_threshold = db.Column(db.Float, default=0.3)
    vector_max_results = db.Column(db.Integer, default=50)
    webdav_url = db.Column(db.String(512), nullable=True)
    webdav_username = db.Column(db.String(256), nullable=True)
    webdav_password = db.Column(db.String(512), nullable=True)
    webdav_path = db.Column(db.String(512), default='/nav_backups/')
    webdav_auto_backup = db.Column(db.Boolean, default=False)
    webdav_backup_interval = db.Column(db.Integer, default=24)
    webdav_backup_keep_count = db.Column(db.Integer, default=10)
    webdav_last_backup_time = db.Column(db.DateTime, nullable=True)
    webdav_last_backup_status = db.Column(db.String(256), nullable=True)

    @staticmethod
    def get_default_qdrant_url():
        import os

        is_docker = False
        try:
            if os.path.exists('/.dockerenv'):
                is_docker = True
            elif os.environ.get('DOCKER_CONTAINER') == 'true':
                is_docker = True
            elif os.path.exists('/proc/self/cgroup'):
                with open('/proc/self/cgroup', 'r') as file_obj:
                    if 'docker' in file_obj.read():
                        is_docker = True
        except Exception:
            pass

        return 'http://qdrant:6333' if is_docker else 'http://localhost:6333'

    @classmethod
    def _database_path(cls):
        db_uri = None
        if has_app_context():
            db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI')
        if not db_uri:
            db_uri = getattr(Config, 'SQLALCHEMY_DATABASE_URI', None)
        if not db_uri or not db_uri.startswith('sqlite:///'):
            return None
        return db_uri.replace('sqlite:///', '', 1)

    @classmethod
    def _repair_legacy_schema(cls):
        db_path = cls._database_path()
        if not db_path:
            return False

        from app.utils.db_migration import migrate_ai_provider_config_table, migrate_site_settings_fields, migrate_webdav_config_table
        from app.utils.icon_db_migration import migrate_icon_management_tables

        migrate_site_settings_fields(db_path)
        migrate_webdav_config_table(db_path)
        migrate_ai_provider_config_table(db_path)
        migrate_icon_management_tables(db_path)
        return True

    @classmethod
    def get_settings(cls):
        try:
            settings = cls.query.first()
        except OperationalError as exc:
            message = str(exc).lower()
            if 'site_settings' not in message or 'no such column' not in message:
                raise
            db.session.rollback()
            db.session.remove()
            repaired = cls._repair_legacy_schema()
            if not repaired:
                raise
            settings = cls.query.first()

        if not settings:
            settings = cls()
            db.session.add(settings)
            db.session.commit()

        if settings.ensure_legacy_ai_provider():
            settings.sync_legacy_ai_fields_from_providers()
            db.session.commit()

        return settings

    def get_embedding_api_config(self):
        if self.embedding_api_base_url and self.embedding_api_key:
            return self.embedding_api_base_url, self.embedding_api_key
        provider = self.get_primary_ai_provider(enabled_only=True) or self.get_primary_ai_provider(enabled_only=False)
        if provider and provider.api_base_url and provider.api_key:
            return provider.api_base_url, provider.api_key
        return self.ai_api_base_url, self.ai_api_key

    def get_icon_imagebed_config(self):
        provider = (self.icon_imagebed_provider or '').strip().lower()
        api_url = (self.icon_imagebed_api_url or '').strip()
        token = (self.icon_imagebed_token or '').strip()
        return provider, api_url, token

    def get_icon_source_providers(self):
        from app.main.utils import merge_icon_source_providers
        return merge_icon_source_providers(self.icon_source_providers_json)

    def set_icon_source_providers(self, providers):
        self.icon_source_providers_json = json.dumps(providers or [], ensure_ascii=False)

    def get_ai_model_catalog(self):
        if not self.ai_model_catalog_json:
            return []
        try:
            payload = json.loads(self.ai_model_catalog_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get('models'), list):
            return payload['models']
        return []

    def set_ai_model_catalog(self, models):
        self.ai_model_catalog_json = json.dumps(models or [], ensure_ascii=False)

    def get_ai_selected_models(self):
        return {
            'intent': self.ai_selected_intent_model or '',
            'rerank': self.ai_selected_rerank_model or '',
            'translate': self.ai_selected_translate_model or '',
            'site_info': self.ai_selected_site_info_model or '',
            'fallback': self.ai_selected_fallback_model or '',
        }

    def get_ai_providers(self, enabled_only=False):
        return AIProviderConfig.ordered_query(enabled_only=enabled_only).all()

    def get_primary_ai_provider(self, enabled_only=True):
        providers = self.get_ai_providers(enabled_only=enabled_only)
        if providers:
            return providers[0]
        if enabled_only:
            providers = self.get_ai_providers(enabled_only=False)
            if providers:
                return providers[0]
        return None

    def get_ai_task_bindings(self):
        defaults = {
            'intent': {'mode': 'auto', 'provider_id': None, 'model_name': ''},
            'rerank': {'mode': 'auto', 'provider_id': None, 'model_name': ''},
            'translate': {'mode': 'auto', 'provider_id': None, 'model_name': ''},
            'site_info': {'mode': 'auto', 'provider_id': None, 'model_name': ''},
        }
        if not self.ai_task_bindings_json:
            return defaults
        try:
            payload = json.loads(self.ai_task_bindings_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return defaults
        if not isinstance(payload, dict):
            return defaults

        normalized = {}
        for task_key, default_value in defaults.items():
            raw_binding = payload.get(task_key)
            if not isinstance(raw_binding, dict):
                normalized[task_key] = default_value.copy()
                continue

            provider_id = raw_binding.get('provider_id')
            try:
                provider_id = int(provider_id) if provider_id not in (None, '', False) else None
            except (TypeError, ValueError):
                provider_id = None

            mode = (raw_binding.get('mode') or 'auto').strip().lower()
            if mode not in {'auto', 'manual'}:
                mode = 'auto'

            model_name = raw_binding.get('model_name')
            model_name = model_name.strip() if isinstance(model_name, str) else ''

            normalized[task_key] = {
                'mode': mode,
                'provider_id': provider_id,
                'model_name': model_name,
            }

        return normalized

    def set_ai_task_bindings(self, bindings):
        payload = {}
        if not isinstance(bindings, dict):
            bindings = {}
        for task_key in ('intent', 'rerank', 'translate', 'site_info'):
            raw_binding = bindings.get(task_key)
            if not isinstance(raw_binding, dict):
                raw_binding = {}
            provider_id = raw_binding.get('provider_id')
            try:
                provider_id = int(provider_id) if provider_id not in (None, '', False) else None
            except (TypeError, ValueError):
                provider_id = None
            mode = (raw_binding.get('mode') or 'auto').strip().lower()
            if mode not in {'auto', 'manual'}:
                mode = 'auto'
            model_name = raw_binding.get('model_name')
            model_name = model_name.strip() if isinstance(model_name, str) else ''
            payload[task_key] = {'mode': mode, 'provider_id': provider_id, 'model_name': model_name}
        self.ai_task_bindings_json = json.dumps(payload, ensure_ascii=False)

    def get_ai_task_test_results(self):
        defaults = {
            'intent': {'status': 'idle', 'message': '', 'provider_id': None, 'provider_name': '', 'model_name': '', 'tested_at': '', 'protocol': ''},
            'rerank': {'status': 'idle', 'message': '', 'provider_id': None, 'provider_name': '', 'model_name': '', 'tested_at': '', 'protocol': ''},
            'translate': {'status': 'idle', 'message': '', 'provider_id': None, 'provider_name': '', 'model_name': '', 'tested_at': '', 'protocol': ''},
            'site_info': {'status': 'idle', 'message': '', 'provider_id': None, 'provider_name': '', 'model_name': '', 'tested_at': '', 'protocol': ''},
        }
        if not self.ai_task_test_results_json:
            return defaults
        try:
            payload = json.loads(self.ai_task_test_results_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return defaults
        if not isinstance(payload, dict):
            return defaults

        normalized = {}
        for task_key, default_value in defaults.items():
            raw_result = payload.get(task_key)
            if not isinstance(raw_result, dict):
                normalized[task_key] = default_value.copy()
                continue
            provider_id = raw_result.get('provider_id')
            try:
                provider_id = int(provider_id) if provider_id not in (None, '', False) else None
            except (TypeError, ValueError):
                provider_id = None
            status = (raw_result.get('status') or 'idle').strip().lower()
            if status not in {'idle', 'success', 'error'}:
                status = 'idle'
            normalized[task_key] = {
                'status': status,
                'message': raw_result.get('message', '') or '',
                'provider_id': provider_id,
                'provider_name': raw_result.get('provider_name', '') or '',
                'model_name': raw_result.get('model_name', '') or '',
                'tested_at': raw_result.get('tested_at', '') or '',
                'protocol': raw_result.get('protocol', '') or '',
            }
        return normalized

    def set_ai_task_test_results(self, results):
        payload = {}
        if not isinstance(results, dict):
            results = {}
        for task_key in ('intent', 'rerank', 'translate', 'site_info'):
            raw_result = results.get(task_key)
            if not isinstance(raw_result, dict):
                raw_result = {}
            provider_id = raw_result.get('provider_id')
            try:
                provider_id = int(provider_id) if provider_id not in (None, '', False) else None
            except (TypeError, ValueError):
                provider_id = None
            status = (raw_result.get('status') or 'idle').strip().lower()
            if status not in {'idle', 'success', 'error'}:
                status = 'idle'
            payload[task_key] = {
                'status': status,
                'message': raw_result.get('message', '') or '',
                'provider_id': provider_id,
                'provider_name': raw_result.get('provider_name', '') or '',
                'model_name': raw_result.get('model_name', '') or '',
                'tested_at': raw_result.get('tested_at', '') or '',
                'protocol': raw_result.get('protocol', '') or '',
            }
        self.ai_task_test_results_json = json.dumps(payload, ensure_ascii=False)

    def ensure_legacy_ai_provider(self):
        if AIProviderConfig.query.count() > 0:
            return False
        api_base_url = (self.ai_api_base_url or '').strip()
        api_key = (self.ai_api_key or '').strip()
        if not api_base_url or not api_key:
            return False
        provider = AIProviderConfig(
            name='默认 AI 提供方',
            api_base_url=api_base_url,
            api_key=api_key,
            interface_mode=(self.ai_interface_mode or 'auto').strip().lower() or 'auto',
            enabled=True,
            priority=100,
            probe_last_at=self.ai_model_probe_last_at,
            probe_error=self.ai_model_probe_error,
            probe_signature=self.ai_model_probe_signature,
        )
        provider.set_model_catalog(self.get_ai_model_catalog())
        provider.set_recommended_models(self.get_ai_selected_models())
        db.session.add(provider)
        db.session.flush()
        if not self.ai_task_bindings_json:
            bindings = {
                'intent': {'mode': 'auto', 'provider_id': provider.id, 'model_name': ''},
                'rerank': {'mode': 'auto', 'provider_id': provider.id, 'model_name': ''},
                'translate': {'mode': 'auto', 'provider_id': provider.id, 'model_name': ''},
                'site_info': {'mode': 'auto', 'provider_id': provider.id, 'model_name': ''},
            }
            legacy_manual_model = (self.ai_model_name or '').strip()
            if legacy_manual_model and not bool(getattr(self, 'ai_auto_model_selection_enabled', False)):
                for task_key in bindings.keys():
                    bindings[task_key]['mode'] = 'manual'
                    bindings[task_key]['model_name'] = legacy_manual_model
            self.set_ai_task_bindings(bindings)
        if not self.ai_task_test_results_json:
            self.set_ai_task_test_results({})
        return True

    def sync_legacy_ai_fields_from_providers(self):
        provider = self.get_primary_ai_provider(enabled_only=True) or self.get_primary_ai_provider(enabled_only=False)
        if not provider:
            self.ai_api_base_url = None
            self.ai_api_key = None
            self.ai_interface_mode = 'auto'
            self.ai_model_catalog_json = None
            self.ai_model_probe_last_at = None
            self.ai_model_probe_error = None
            self.ai_model_probe_signature = None
            self.ai_selected_intent_model = None
            self.ai_selected_rerank_model = None
            self.ai_selected_translate_model = None
            self.ai_selected_site_info_model = None
            self.ai_selected_fallback_model = None
            self.ai_auto_model_selection_enabled = False
            self.ai_model_name = None
            return
        self.ai_api_base_url = provider.api_base_url
        self.ai_api_key = provider.api_key
        self.ai_interface_mode = (provider.interface_mode or 'auto').strip().lower() or 'auto'
        self.ai_model_catalog_json = provider.model_catalog_json
        self.ai_model_probe_last_at = provider.probe_last_at
        self.ai_model_probe_error = provider.probe_error
        self.ai_model_probe_signature = provider.probe_signature
        recommended = provider.get_recommended_models()
        self.ai_selected_intent_model = recommended.get('intent') or None
        self.ai_selected_rerank_model = recommended.get('rerank') or None
        self.ai_selected_translate_model = recommended.get('translate') or None
        self.ai_selected_site_info_model = recommended.get('site_info') or None
        self.ai_selected_fallback_model = recommended.get('fallback') or None
        bindings = self.get_ai_task_bindings()
        self.ai_auto_model_selection_enabled = any(binding.get('mode') == 'auto' for binding in bindings.values())
        first_manual_model = ''
        for binding in bindings.values():
            if binding.get('mode') == 'manual' and binding.get('model_name'):
                first_manual_model = binding['model_name']
                break
        self.ai_model_name = first_manual_model or recommended.get('fallback') or self.ai_model_name

    def __repr__(self):
        return f'<SiteSettings {self.site_name}>'


class WebDAVConfig(db.Model):
    __tablename__ = 'webdav_config'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, default='鎴戠殑浜戠澶囦唤')
    webdav_url = db.Column(db.String(512), nullable=True)
    webdav_username = db.Column(db.String(256), nullable=True)
    webdav_password = db.Column(db.String(512), nullable=True)  # 鍔犲瘑瀛樺偍
    webdav_path = db.Column(db.String(512), default='/nav_backups/')
    enabled = db.Column(db.Boolean, default=True)
    
    # 鑷姩澶囦唤璁剧疆
    auto_backup = db.Column(db.Boolean, default=False)
    backup_interval = db.Column(db.Integer, default=24)       # 澶囦唤闂撮殧锛堝皬鏃讹級
    backup_keep_count = db.Column(db.Integer, default=10)     # 杩滅淇濈暀浠芥暟
    last_backup_time = db.Column(db.DateTime, nullable=True)
    last_backup_status = db.Column(db.String(256), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        """Serialize to a dictionary for JSON responses."""
        status_text = self.last_backup_status or ''
        status_type = 'none'
        status_msg = '浠庢湭澶囦唤'
        if status_text:
            parts = status_text.split('|', 1)
            if len(parts) == 2:
                status_type = parts[0]
                status_msg = parts[1]
            else:
                status_msg = status_text
        
        return {
            'id': self.id,
            'name': self.name,
            'webdav_url': self.webdav_url or '',
            'webdav_username': self.webdav_username or '',
            'has_password': bool(self.webdav_password),
            'webdav_path': self.webdav_path or '/nav_backups/',
            'enabled': self.enabled,
            'auto_backup': self.auto_backup,
            'backup_interval': self.backup_interval or 24,
            'backup_keep_count': self.backup_keep_count or 10,
            'last_backup_time': self.last_backup_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_backup_time else '',
            'status_type': status_type,
            'status_msg': status_msg,
        }
    
    def __repr__(self):
        return f'<WebDAVConfig {self.name}>'


class Background(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128))  # 鑳屾櫙鍚嶇О
    url = db.Column(db.String(512))  # 鑳屾櫙URL
    type = db.Column(db.String(32))  # 鑳屾櫙绫诲瀷锛歩mage, gradient, color
    device_type = db.Column(db.String(32))  # 璁惧绫诲瀷锛歱c, mobile, both
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    created_by = db.relationship('User', backref='backgrounds')
    
    def __repr__(self):
        return f'<Background {self.title}>'


class OperationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    operation_type = db.Column(db.String(50))  # ADD, MODIFY, DELETE
    website_id = db.Column(db.Integer, nullable=True)  # 鍙互涓虹┖锛岃〃绀鸿褰曞凡琚垹闄ょ殑缃戠珯
    website_title = db.Column(db.String(128), nullable=True)
    website_url = db.Column(db.String(256), nullable=True)
    website_icon = db.Column(db.String(256), nullable=True)
    category_id = db.Column(db.Integer, nullable=True)
    category_name = db.Column(db.String(64), nullable=True)
    details = db.Column(db.Text, nullable=True)  # 瀛樺偍鏇村鎿嶄綔缁嗚妭锛孞SON鏍煎紡
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='operations')
    
    def __repr__(self):
        return f'<OperationLog {self.operation_type} - {self.website_title}>'


class DeadlinkCheck(db.Model):
    """Dead link check record."""
    id = db.Column(db.Integer, primary_key=True)
    check_id = db.Column(db.String(36), index=True)  # 妫€娴嬫壒娆D锛屼娇鐢║UID
    website_id = db.Column(db.Integer, db.ForeignKey('website.id'), nullable=False)
    url = db.Column(db.String(256), nullable=False)
    is_valid = db.Column(db.Boolean, default=True)  # True: 鏈夋晥閾炬帴, False: 鏃犳晥閾炬帴
    status_code = db.Column(db.Integer, nullable=True)  # HTTP鐘舵€佺爜
    error_type = db.Column(db.String(50), nullable=True)  # 閿欒绫诲瀷: timeout, connection_error, etc.
    error_message = db.Column(db.Text, nullable=True)  # 璇︾粏閿欒淇℃伅
    response_time = db.Column(db.Float, nullable=True)  # 鍝嶅簲鏃堕棿(绉?
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 鍏崇郴瀹氫箟
    website = db.relationship('Website', backref='deadlink_checks')
    
    def __repr__(self):
        return f'<DeadlinkCheck {self.url} - {"Valid" if self.is_valid else "Invalid"}>'
