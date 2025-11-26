#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""操作日志管理路由"""

from flask import request, jsonify, current_app
from flask_login import login_required
from app import db
from app.admin import bp
from app.admin.decorators import admin_required
from app.models import OperationLog, User


@bp.route('/api/operation-log/delete', methods=['POST'])
@login_required
@admin_required
def delete_operation_log():
    data = request.json
    log_id = data.get('id')
    
    if not log_id:
        return jsonify({'success': False, 'message': '未提供日志ID'})
    
    log = OperationLog.query.get(log_id)
    if not log:
        return jsonify({'success': False, 'message': '日志不存在'})
    
    db.session.delete(log)
    db.session.commit()
    
    return jsonify({'success': True, 'message': '日志删除成功'})


@bp.route('/api/operation-log/batch-delete', methods=['POST'])
@login_required
@admin_required
def batch_delete_operation_logs():
    data = request.json
    log_ids = data.get('ids', [])
    
    if not log_ids:
        return jsonify({'success': False, 'message': '未提供日志ID'})
    
    logs = OperationLog.query.filter(OperationLog.id.in_(log_ids)).all()
    for log in logs:
        db.session.delete(log)
    
    db.session.commit()
    
    return jsonify({'success': True, 'message': f'已删除 {len(logs)} 条日志'})


@bp.route('/api/operation-log/clear-all/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def clear_all_operation_logs(user_id):
    """清空指定用户的所有操作记录"""
    try:
        # 验证用户存在
        user = User.query.get_or_404(user_id)
        
        # 获取用户所有操作记录数量
        count = OperationLog.query.filter_by(user_id=user_id).count()
        
        # 删除所有操作记录
        OperationLog.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'已清空用户 {user.username} 的所有操作记录，共 {count} 条'
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"清空操作记录失败: {str(e)}")
        return jsonify({'success': False, 'message': f'操作失败: {str(e)}'}), 500

