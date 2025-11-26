from flask import Blueprint

bp = Blueprint('main', __name__)

# 导入所有路由模块
from app.main import views, api_search, api_website, api_utils 