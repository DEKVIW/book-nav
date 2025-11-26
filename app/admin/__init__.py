from flask import Blueprint

bp = Blueprint('admin', __name__)

# 导入所有路由模块（确保路由被注册）
from app.admin import routes  # 保留首页路由
from app.admin import categories, websites, invitations, users, site_settings, operation_logs
from app.admin import data_management, backups, icon_fetch, wallpapers, deadlinks, vector_indexing 