#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量生成网站向量索引脚本
用于为数据库中所有现有网站生成向量并存储到 Qdrant

使用方法：
    python batch_generate_vectors.py

功能：
    - 遍历数据库中所有网站
    - 为每个网站生成向量并存储到 Qdrant
    - 显示进度和统计信息
    - 支持断点续传（跳过已存在的向量）
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import create_app, db
from app.models import Website, Category, SiteSettings
from app.utils.vector_service import EmbeddingClient, QdrantVectorStore, VectorSearchService
from qdrant_client import QdrantClient


def check_existing_vectors(qdrant_url: str, website_ids: list) -> set:
    """
    检查 Qdrant 中已存在的向量（用于跳过已生成的网站）
    
    Args:
        qdrant_url: Qdrant 服务地址
        website_ids: 网站ID列表
        
    Returns:
        已存在向量的网站ID集合
    """
    try:
        client = QdrantClient(url=qdrant_url)
        collections = client.get_collections().collections
        collection_names = [c.name for c in collections]
        
        if 'websites' not in collection_names:
            return set()
        
        # 批量查询已存在的向量
        # 注意：Qdrant中point的id就是website_id
        existing_ids = set()
        batch_size = 100
        
        for i in range(0, len(website_ids), batch_size):
            batch_ids = website_ids[i:i+batch_size]
            try:
                # 使用 retrieve 方法批量获取这些ID的向量
                # 如果ID存在，说明向量已生成
                result = client.retrieve(
                    collection_name='websites',
                    ids=batch_ids
                )
                # 从返回结果中提取ID（成功返回的说明已存在）
                for point in result:
                    existing_ids.add(point.id)
            except Exception as e:
                # 如果查询失败，继续处理（可能是ID不存在或其他问题）
                # 这种情况下认为向量不存在，继续生成
                pass
        
        return existing_ids
    except Exception as e:
        print(f"  警告：检查已存在向量时出错: {str(e)}")
        print(f"  将跳过检查，继续生成所有向量")
        return set()


def generate_all_vectors(skip_existing: bool = True, batch_size: int = 10):
    """
    为所有网站生成向量
    
    Args:
        skip_existing: 是否跳过已存在的向量
        batch_size: 每批处理的网站数量（用于进度显示）
    """
    app = create_app()
    
    with app.app_context():
        # 获取配置
        settings = SiteSettings.get_settings()
        
        if not settings:
            print("❌ 无法获取站点设置")
            return
        
        if not all([settings.ai_api_base_url, settings.ai_api_key, settings.embedding_model]):
            print("❌ AI搜索配置不完整，请先配置：")
            print("   - API基础URL")
            print("   - API密钥")
            print("   - Embedding模型")
            return
        
        if not settings.qdrant_url:
            print("❌ Qdrant URL 未配置")
            return
        
        if not settings.vector_search_enabled:
            print("⚠️  向量搜索未启用，是否继续？(y/n): ", end='')
            choice = input().strip().lower()
            if choice != 'y':
                print("已取消")
                return
        
        print(f"\n📋 配置信息：")
        print(f"   API地址: {settings.ai_api_base_url}")
        print(f"   Embedding模型: {settings.embedding_model}")
        print(f"   Qdrant地址: {settings.qdrant_url}")
        print()
        
        # 初始化向量服务
        try:
            embedding_client = EmbeddingClient(
                api_base_url=settings.ai_api_base_url,
                api_key=settings.ai_api_key,
                model_name=settings.embedding_model or 'text-embedding-3-small'
            )
            vector_store = QdrantVectorStore(
                qdrant_url=settings.qdrant_url,
                vector_dimension=embedding_client.dimension
            )
            vector_service = VectorSearchService(embedding_client, vector_store)
            
            print(f"✅ 向量服务初始化成功（维度: {embedding_client.dimension}）")
            print()
        except Exception as e:
            print(f"❌ 向量服务初始化失败: {str(e)}")
            return
        
        # 获取所有网站
        websites = Website.query.all()
        total_count = len(websites)
        
        if total_count == 0:
            print("❌ 没有找到网站")
            return
        
        print(f"📊 找到 {total_count} 个网站")
        
        # 检查已存在的向量
        existing_ids = set()
        if skip_existing:
            print("🔍 检查已存在的向量...")
            website_ids = [w.id for w in websites]
            existing_ids = check_existing_vectors(settings.qdrant_url, website_ids)
            if existing_ids:
                print(f"   ✅ 发现 {len(existing_ids)} 个网站已有向量，将跳过")
            else:
                print(f"   ℹ️  未发现已存在的向量，将全部生成")
            print()
        
        # 统计信息
        success_count = 0
        fail_count = 0
        skip_count = 0
        
        print(f"🚀 开始生成向量...")
        print("=" * 60)
        
        # 遍历所有网站
        for idx, website in enumerate(websites, 1):
            # 检查是否已存在
            if skip_existing and website.id in existing_ids:
                skip_count += 1
                if idx % batch_size == 0 or idx == total_count:
                    print(f"[{idx}/{total_count}] ⏭️  {website.title} - 已存在，跳过")
                continue
            
            try:
                # 获取分类名称
                category_name = ""
                if website.category_id:
                    category = Category.query.get(website.category_id)
                    if category:
                        category_name = category.name
                
                # 生成向量
                success = vector_service.index_website(
                    website_id=website.id,
                    title=website.title or "",
                    description=website.description or "",
                    category_name=category_name,
                    url=website.url or ""
                )
                
                if success:
                    success_count += 1
                    # 显示进度（每batch_size个或最后一个显示）
                    if idx % batch_size == 0 or idx == total_count:
                        print(f"[{idx}/{total_count}] ✅ {website.title} - 向量生成成功")
                else:
                    fail_count += 1
                    print(f"[{idx}/{total_count}] ❌ {website.title} - 向量生成失败")
                    
            except Exception as e:
                fail_count += 1
                print(f"[{idx}/{total_count}] ❌ {website.title} - 错误: {str(e)}")
        
        print()
        print("=" * 60)
        print(f"📊 向量生成完成！")
        print(f"   ✅ 成功: {success_count}")
        print(f"   ⏭️  跳过: {skip_count}")
        print(f"   ❌ 失败: {fail_count}")
        print(f"   📈 总计: {total_count}")
        print("=" * 60)


if __name__ == '__main__':
    try:
        import argparse
        
        parser = argparse.ArgumentParser(description='批量生成网站向量索引')
        parser.add_argument('--no-skip', action='store_true', 
                          help='不跳过已存在的向量（重新生成所有向量）')
        parser.add_argument('--batch-size', type=int, default=10,
                          help='进度显示批次大小（默认：10）')
        
        args = parser.parse_args()
        
        generate_all_vectors(
            skip_existing=not args.no_skip,
            batch_size=args.batch_size
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断操作")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

