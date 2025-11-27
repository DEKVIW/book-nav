from datetime import datetime
from flask import render_template, redirect, url_for, flash, request, session, jsonify
from flask_login import current_user, login_user, logout_user, login_required
from app import db, csrf
from app.auth import bp
from app.auth.forms import LoginForm, RegistrationForm
from app.models import User, InvitationCode
from werkzeug.urls import url_parse

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.check_password(form.password.data):
            flash('用户名或密码错误，如果没有账户请先注册', 'danger')
            return redirect(url_for('auth.login'))
        login_user(user, remember=form.remember_me.data)
        session.permanent = True
        next_page = request.args.get('next')
        if not next_page or url_parse(next_page).netloc != '':
            next_page = url_for('main.index')
        return redirect(next_page)
    return render_template('auth/login.html', title='登录', form=form)

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.index'))

@bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        invitation_code = InvitationCode.query.filter_by(code=form.invitation_code.data, is_active=True).first()
        
        if invitation_code and invitation_code.used_by_id is None:
            user = User(username=form.username.data, email=form.email.data)
            user.set_password(form.password.data)
            
            invitation_code.used_by_id = user.id
            invitation_code.used_at = datetime.utcnow()
            invitation_code.is_active = False
            
            db.session.add(user)
            db.session.commit()
            
            flash('注册成功，请登录', 'success')
            return redirect(url_for('auth.login'))
        
        flash('注册失败，邀请码无效', 'danger')
    return render_template('auth/register.html', title='注册', form=form)

@bp.route('/refresh-csrf')
@login_required
def refresh_csrf():
    """刷新CSRF令牌的API接口"""
    try:
        # 生成新的CSRF令牌
        from flask_wtf.csrf import generate_csrf
        new_token = generate_csrf()
        return jsonify({
            'success': True,
            'csrf_token': new_token
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'刷新CSRF令牌失败: {str(e)}'
        }), 500

@bp.route('/check-csrf')
@login_required
def check_csrf():
    """检查CSRF令牌有效性的API接口"""
    try:
        # 验证当前CSRF令牌是否有效
        token = request.args.get('token') or request.headers.get('X-CSRFToken')
        if token and csrf._validate_csrf(token):
            return jsonify({
                'success': True,
                'valid': True
            })
        else:
            return jsonify({
                'success': True,
                'valid': False
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'检查CSRF令牌失败: {str(e)}'
        }), 500 