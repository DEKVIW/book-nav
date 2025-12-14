from flask import jsonify, request
from flask_login import current_user, login_required
from app import db, csrf
from app.api import bp
from app.models import Website, Category, OperationLog
import json

@bp.route('/website/<int:id>/delete', methods=['DELETE'])
@login_required
def delete_website(id):
    if not current_user.is_admin:
        return jsonify({
            'success': False,
            'message': '您没有权限执行此操作'
        }), 403

    website = Website.query.get_or_404(id)
    
    try:
        # 记录删除操作
        details = {
            'description': website.description,
            'is_private': website.is_private,
            'is_featured': website.is_featured
        }
        
        operation_log = OperationLog(
            user_id=current_user.id,
            operation_type='DELETE',
            website_id=None,  # 删除后ID不存在
            website_title=website.title,
            website_url=website.url,
            website_icon=website.icon,
            category_id=website.category_id,
            category_name=website.category.name if website.category else None,
            details=json.dumps(details)
        )
        
        db.session.add(operation_log)
        
        # 在删除数据库记录之前，先保存网站ID
        website_id = website.id
        db.session.delete(website)
        db.session.commit()
        
        # 删除向量数据（在数据库提交成功后，避免影响事务）
        try:
            from app.utils.vector_service import delete_website_vector
            delete_website_vector(website_id)
        except Exception as e:
            # 向量删除失败不应该影响网站删除，只记录日志
            current_app.logger.warning(f"删除网站向量时出错: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': '网站删除成功'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': '删除失败：' + str(e)
        }), 500

@bp.route('/website/update/<int:id>', methods=['POST'])
@login_required
def update_website(id):
    if not current_user.is_admin:
        return jsonify({
            'success': False,
            'message': '您没有权限执行此操作'
        }), 403

    website = Website.query.get_or_404(id)
    data = request.get_json()

    try:
        # 记录修改前的状态
        old_title = website.title
        old_url = website.url
        old_description = website.description
        old_icon = website.icon
        old_category_id = website.category_id
        old_category_name = website.category.name if website.category else None
        old_is_private = website.is_private
        old_is_featured = website.is_featured
        old_sort_order = website.sort_order
        
        # 更新网站信息
        website.title = data.get('title', website.title)
        website.url = data.get('url', website.url)
        website.description = data.get('description', website.description)
        website.icon = data.get('icon', website.icon)
        
        if 'category_id' in data and data['category_id']:
            website.category_id = data['category_id']
        
        if 'is_private' in data:
            website.is_private = bool(data['is_private'])
            
        if 'sort_order' in data:
            website.sort_order = int(data['sort_order'])
        
        # 确定哪些字段发生了变化
        changes = {}
        if old_title != website.title:
            changes['title'] = {'old': old_title, 'new': website.title}
        if old_url != website.url:
            changes['url'] = {'old': old_url, 'new': website.url}
        if old_description != website.description:
            changes['description'] = {'old': old_description, 'new': website.description}
        if old_icon != website.icon:
            changes['icon'] = {'old': old_icon, 'new': website.icon}
        if old_sort_order != website.sort_order:
            changes['sort_order'] = {'old': old_sort_order, 'new': website.sort_order}
            
        if old_category_id != website.category_id:
            new_category_name = website.category.name if website.category else None
            changes['category'] = {
                'old': old_category_name, 
                'new': new_category_name
            }
            
        if old_is_private != website.is_private:
            changes['is_private'] = {'old': old_is_private, 'new': website.is_private}
        
        # 如果有变化，记录修改操作
        if changes:
            operation_log = OperationLog(
                user_id=current_user.id,
                operation_type='MODIFY',
                website_id=website.id,
                website_title=website.title,
                website_url=website.url,
                website_icon=website.icon,
                category_id=website.category_id,
                category_name=website.category.name if website.category else None,
                details=json.dumps(changes)
            )
            db.session.add(operation_log)
        
        db.session.commit()
        
        # 检查是否需要更新向量（标题、描述或分类变化时）
        needs_vector_update = (
            old_title != website.title or
            old_description != website.description or
            old_category_id != website.category_id
        )
        
        if needs_vector_update:
            try:
                from app.main.api_website import _trigger_vector_indexing
                new_category_name = website.category.name if website.category else None
                _trigger_vector_indexing(website.id, new_category_name)
            except Exception as e:
                current_app.logger.warning(f"触发向量更新失败: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': '网站信息更新成功'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': '更新失败：' + str(e)
        }), 500

@bp.route('/category/update_order', methods=['POST'])
@login_required
@csrf.exempt
def update_category_order():
    """更新分类排序顺序的API接口"""
    # 检查当前用户是否为管理员
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    # 获取请求数据
    data = request.get_json()
    if not data or 'items' not in data:
        return jsonify({'success': False, 'message': '无效的请求数据'}), 400
    
    items = data['items']
    print(f"收到分类排序请求: {items}") # 记录排序数据
    
    try:
        # 更新每个分类的排序顺序
        updated_count = 0
        for item in items:
            category_id = item.get('id')
            order = item.get('order')
            
            if category_id is not None and order is not None:
                category = Category.query.get(category_id)
                if category:
                    old_order = category.order
                    category.order = order
                    updated_count += 1
                    print(f"更新分类ID {category_id} 排序: {old_order} -> {order}")
        
        # 保存更改
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'分类排序已更新 ({updated_count} 个分类)'
        })
    except Exception as e:
        db.session.rollback()
        print(f"分类排序更新失败: {str(e)}")
        return jsonify({'success': False, 'message': f'更新排序失败: {str(e)}'}), 500 

@bp.route('/record-visit/<int:website_id>', methods=['POST'])
def record_visit(website_id):
    """记录网站访问次数的API接口"""
    try:
        website = Website.query.get_or_404(website_id)
        
        # 增加网站浏览次数
        website.views += 1
        
        # 记录今日访问量
        from datetime import datetime, timedelta
        today = datetime.utcnow().date()
        
        # 如果最后访问日期为空或不是今天，重置今日访问量
        if website.last_view is None or website.last_view.date() < today:
            website.views_today = 1
        else:
            website.views_today += 1
        
        # 更新最后访问时间
        website.last_view = datetime.utcnow()
        
        db.session.commit()
        return jsonify({'success': True, 'message': '访问记录已更新'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'记录访问失败: {str(e)}'}), 500 