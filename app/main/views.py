#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""页面视图路由"""

from flask import render_template, redirect, url_for, flash, request
from flask_login import current_user, login_required
from app import db
from app.main import bp
from app.models import Category, Website, SiteSettings
from app.main.forms import WebsiteForm
from datetime import datetime


@bp.route('/')
def index():
    """首页"""
    # 只获取一级分类（parent_id为None的分类）
    categories = Category.query.filter_by(parent_id=None).order_by(Category.order.desc()).all()
    
    featured_sites_query = Website.query.filter_by(is_featured=True)
    if not current_user.is_authenticated:
        featured_sites_query = featured_sites_query.filter_by(is_private=False)
    elif not current_user.is_admin:
        featured_sites_query = featured_sites_query.filter(
            (Website.is_private == False) |
            (Website.created_by_id == current_user.id) |
            (Website.visible_to.contains(str(current_user.id)))
        )
    featured_sites = featured_sites_query.order_by(Website.views.desc()).limit(6).all()
    
    for category in categories:
        websites_query = Website.query.filter_by(category_id=category.id)
        if not current_user.is_authenticated:
            websites_query = websites_query.filter_by(is_private=False)
        elif not current_user.is_admin:
            websites_query = websites_query.filter(
                (Website.is_private == False) |
                (Website.created_by_id == current_user.id) |
                (Website.visible_to.contains(str(current_user.id)))
            )
        
        # 计算一级分类下直接链接的数量（直接属于一级分类的网站）
        category.direct_website_count = websites_query.count()
        
        # 获取所有子分类并计算每个子分类的网站数量
        children = category.children.order_by(Category.order.desc()).all()
        children_total_count = 0
        for child in children:
            child_query = Website.query.filter_by(category_id=child.id)
            if not current_user.is_authenticated:
                child_query = child_query.filter_by(is_private=False)
            elif not current_user.is_admin:
                child_query = child_query.filter(
                    (Website.is_private == False) |
                    (Website.created_by_id == current_user.id) |
                    (Website.visible_to.contains(str(current_user.id)))
                )
            # 计算子分类的网站数量
            child.total_count = child_query.count()
            children_total_count += child.total_count
        
        # 将处理过的子分类列表存储到category对象，供模板使用（即使为空也要设置）
        category.children_list = children
        
        # 如果一级分类有子分类
        if len(children) > 0:
            # 一级分类下未分类的链接数量 = 直接链接数量（直接属于一级分类的就是未分类的）
            category.total_count = category.direct_website_count
            # 一级分类的总数 = 直接链接数量 + 所有子分类的网站数量
            category.total_count_with_children = category.direct_website_count + children_total_count
            
            # 智能默认显示逻辑：如果未分类网站数量 < display_limit，则显示第一个二级分类的网站
            if category.direct_website_count < category.display_limit:
                # 获取第一个二级分类（权重靠前的，order值最大的）
                first_child = children[0]
                # 查询第一个二级分类的网站
                first_child_query = Website.query.filter_by(category_id=first_child.id)
                if not current_user.is_authenticated:
                    first_child_query = first_child_query.filter_by(is_private=False)
                elif not current_user.is_admin:
                    first_child_query = first_child_query.filter(
                        (Website.is_private == False) |
                        (Website.created_by_id == current_user.id) |
                        (Website.visible_to.contains(str(current_user.id)))
                    )
                # 使用第一个二级分类的网站列表
                category.website_list = first_child_query.order_by(
                    Website.sort_order.desc(),
                    Website.created_at.asc(),
                    Website.views.desc()
                ).limit(category.display_limit).all()
                # 标记当前显示的是第一个子分类（用于可能的后续功能）
                category.displayed_subcategory_id = first_child.id
            else:
                # 未分类网站数量 >= display_limit，显示未分类的网站
                category.website_list = websites_query.order_by(
                    Website.sort_order.desc(),
                    Website.created_at.asc(),
                    Website.views.desc()
                ).limit(category.display_limit).all()
                # 标记当前显示的是未分类
                category.displayed_subcategory_id = None
        else:
            # 如果一级分类没有子分类，直接链接数量就是总数
            category.total_count = category.direct_website_count
            category.total_count_with_children = category.direct_website_count
            # 没有子分类时，直接显示一级分类的网站
            category.website_list = websites_query.order_by(
                Website.sort_order.desc(),
                Website.created_at.asc(),
                Website.views.desc()
            ).limit(category.display_limit).all()
            # 标记当前显示的是主分类
            category.displayed_subcategory_id = None
    
    settings = SiteSettings.get_settings()
    
    return render_template('index.html', 
                           title='首页', 
                           categories=categories, 
                           featured_sites=featured_sites,
                           settings=settings)


@bp.route('/category/<int:id>')
def category(id):
    """分类页面"""
    category = Category.query.get_or_404(id)
    
    highlight_id = request.args.get('highlight')
    
    websites_query = Website.query.filter_by(category_id=id)
    
    if not current_user.is_authenticated:
        websites_query = websites_query.filter_by(is_private=False)
    elif not current_user.is_admin:
        websites_query = websites_query.filter(
            (Website.is_private == False) |
            (Website.created_by_id == current_user.id) |
            (Website.visible_to.contains(str(current_user.id)))
        )
    
    websites = websites_query.order_by(
        Website.sort_order.desc(),
        Website.created_at.asc(), 
        Website.views.desc()
    ).all()
    
    all_categories = Category.query.order_by(Category.order.desc()).all()
    categories = Category.query.filter_by(parent_id=None).order_by(Category.order.desc()).all()
    
    context = {
        'title': category.name,
        'category': category,
        'websites': websites,
        'all_categories': all_categories,
        'categories': categories,
        'highlight_id': highlight_id
    }
    
    if category.parent_id is not None:
        siblings = Category.query.filter_by(parent_id=category.parent_id)\
                                .order_by(Category.order.desc())\
                                .all()
        context['siblings'] = siblings
    
    children = Category.query.filter_by(parent_id=id)\
                            .order_by(Category.order.desc())\
                            .all()
    if children:
        context['children'] = children
        # 计算一级分类下直接链接的数量（未分类的）
        direct_websites_query = Website.query.filter_by(category_id=id)
        if not current_user.is_authenticated:
            direct_websites_query = direct_websites_query.filter_by(is_private=False)
        elif not current_user.is_admin:
            direct_websites_query = direct_websites_query.filter(
                (Website.is_private == False) |
                (Website.created_by_id == current_user.id) |
                (Website.visible_to.contains(str(current_user.id)))
            )
        # 计算所有子分类的网站数量
        children_total_count = 0
        for child in children:
            child_query = Website.query.filter_by(category_id=child.id)
            if not current_user.is_authenticated:
                child_query = child_query.filter_by(is_private=False)
            elif not current_user.is_admin:
                child_query = child_query.filter(
                    (Website.is_private == False) |
                    (Website.created_by_id == current_user.id) |
                    (Website.visible_to.contains(str(current_user.id)))
                )
            child.total_count = child_query.count()
            children_total_count += child.total_count
        # 未分类的数量 = 直接链接数量 - 子分类链接数量
        context['uncategorized_count'] = max(0, direct_websites_query.count() - children_total_count)
    
    return render_template('category.html', **context)


@bp.route('/site/<int:id>')
def site(id):
    """网站详情页（直接跳转）"""
    site = Website.query.get_or_404(id)
    
    site.views += 1
    site.last_view = datetime.utcnow()
    
    db.session.commit()
    return redirect(site.url)


@bp.route('/search')
def search():
    """搜索页面"""
    query = request.args.get('q', '')
    if not query:
        return redirect(url_for('main.index'))
    
    websites_query = Website.query.filter(
        Website.title.contains(query) |
        Website.description.contains(query) |
        Website.url.contains(query)
    )
    
    if not current_user.is_authenticated:
        websites_query = websites_query.filter_by(is_private=False)
    elif not current_user.is_admin:
        websites_query = websites_query.filter(
            (Website.is_private == False) |
            (Website.created_by_id == current_user.id) |
            (Website.visible_to.contains(str(current_user.id)))
        )
    
    websites = websites_query.all()
    return render_template('search.html', 
                         title='搜索结果', 
                         websites=websites, 
                         query=query)


@bp.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    """添加链接页面"""
    form = WebsiteForm()
    form.category_id.choices = [(c.id, c.name) for c in Category.query.order_by(Category.order.desc()).all()]
    form.category_id.choices.insert(0, (0, '-- 请选择分类 --'))
    
    if form.validate_on_submit():
        from app.models import OperationLog
        import json
        
        website = Website(
            title=form.title.data,
            url=form.url.data,
            description=form.description.data,
            icon=form.icon.data,
            category_id=form.category_id.data if form.category_id.data != 0 else None,
            is_featured=False,
            is_private=form.is_private.data,
            sort_order=form.sort_order.data,
            created_by_id=current_user.id
        )
        
        db.session.add(website)
        db.session.commit()
        
        category_name = Category.query.get(form.category_id.data).name if form.category_id.data and form.category_id.data != 0 else None
        operation_log = OperationLog(
            user_id=current_user.id,
            operation_type='ADD',
            website_id=website.id,
            website_title=website.title,
            website_url=website.url,
            website_icon=website.icon,
            category_id=website.category_id,
            category_name=category_name,
            details='{}'
        )
        db.session.add(operation_log)
        db.session.commit()
        
        # 异步生成向量（如果向量搜索已启用）
        try:
            from app.main.api_website import _trigger_vector_indexing
            _trigger_vector_indexing(website.id, category_name)
        except Exception as e:
            current_app.logger.warning(f"触发向量生成失败: {str(e)}")
        
        flash('链接添加成功！', 'success')
        return redirect(url_for('main.add'))
        
    return render_template('add.html', title='添加链接', form=form)


@bp.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit(id):
    """编辑链接页面"""
    website = Website.query.get_or_404(id)
    
    if website.created_by_id != current_user.id and not current_user.is_admin:
        flash('没有权限编辑此链接', 'danger')
        return redirect(url_for('main.index'))
    
    form = WebsiteForm(obj=website)
    form.category_id.choices = [(c.id, c.name) for c in Category.query.order_by(Category.order.desc()).all()]
    form.category_id.choices.insert(0, (0, '-- 请选择分类 --'))
    
    if form.validate_on_submit():
        from app.models import OperationLog
        import json
        
        old_title = website.title
        old_url = website.url
        old_description = website.description
        old_category_id = website.category_id
        old_category_name = website.category.name if website.category else None
        old_is_private = website.is_private
        old_sort_order = website.sort_order
        
        website.title = form.title.data
        website.url = form.url.data
        website.description = form.description.data
        website.icon = form.icon.data
        website.category_id = form.category_id.data if form.category_id.data != 0 else None
        website.is_private = form.is_private.data
        website.sort_order = form.sort_order.data
        
        db.session.commit()
        
        changes = {}
        if old_title != website.title:
            changes['title'] = {'old': old_title, 'new': website.title}
        if old_url != website.url:
            changes['url'] = {'old': old_url, 'new': website.url}
        if old_description != website.description:
            changes['description'] = {'old': old_description, 'new': website.description}
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
        
        flash('链接更新成功！', 'success')
        return redirect(url_for('main.site', id=website.id))
        
    if website.category_id is None:
        form.category_id.data = 0
    
    return render_template('edit.html', title='编辑链接', form=form, website=website)


@bp.route('/delete/<int:id>')
@login_required
def delete(id):
    """删除链接"""
    website = Website.query.get_or_404(id)
    
    if website.created_by_id != current_user.id and not current_user.is_admin:
        flash('没有权限删除此链接', 'danger')
        return redirect(url_for('main.index'))
    
    from app.models import OperationLog
    import json
    
    details = {
        'description': website.description,
        'is_private': website.is_private,
        'is_featured': website.is_featured
    }
    
    operation_log = OperationLog(
        user_id=current_user.id,
        operation_type='DELETE',
        website_id=None,
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
    
    flash('链接删除成功！', 'success')
    return redirect(url_for('main.index'))


@bp.route('/goto/<int:website_id>')
def goto(website_id):
    """跳转页面（带过渡页）"""
    website = Website.query.get_or_404(website_id)
    
    if website.is_private and not current_user.is_authenticated:
        flash('该网站需要登录后才能访问', 'warning')
        return redirect(url_for('auth.login'))
    
    if request.cookies.get('disableRedirect') == 'true':
        website.views += 1
        website.last_view = datetime.utcnow()
        db.session.commit()
        return redirect(website.url)
    
    settings = SiteSettings.query.first()
    
    if current_user.is_authenticated and current_user.is_admin:
        countdown = settings.admin_transition_time
    else:
        countdown = settings.transition_time
    
    website.views += 1
    website.last_view = datetime.utcnow()
    db.session.commit()
    
    return render_template('transition.html',
                         website=website,
                         countdown=countdown,
                         settings=settings)

