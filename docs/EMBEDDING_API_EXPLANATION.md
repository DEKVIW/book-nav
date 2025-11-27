# Embedding API 调用的作用说明

## 为什么需要调用 Embedding API？

### 核心原因：向量数据库只能搜索向量，不能直接搜索文本

```
用户输入文本 "博客"
    ↓
【必须转换】调用 Embedding API
    ↓
转换为向量 [0.123, -0.456, 0.789, ..., 0.234] (1024维)
    ↓
在 Qdrant 中搜索相似向量
    ↓
返回相似度高的网站
```

## 详细流程

### 1. 用户查询阶段（每次搜索都需要）

```python
# app/utils/vector_service.py - VectorSearchService.search()

def search(self, query: str, ...):
    # 步骤1：将用户查询文本转换为向量
    query_vector = self.embedding_client.generate_embedding(query)
    # 例如："博客" → [0.123, -0.456, 0.789, ..., 0.234] (1024维)
    
    # 步骤2：用这个向量在 Qdrant 中搜索
    results = self.vector_store.search_similar(
        query_vector=query_vector,  # 必须是向量，不能是文本
        limit=limit,
        threshold=threshold
    )
    
    return results
```

**为什么必须转换？**
- Qdrant 是**向量数据库**，存储的是数值向量，不是文本
- Qdrant 的 `search()` 方法只接受 `query_vector`（向量），不接受文本
- 必须先将文本转换为向量，才能进行相似度计算

### 2. 网站索引阶段（只需要一次，在添加/更新网站时）

```python
# app/utils/vector_service.py - VectorSearchService.index_website()

def index_website(self, website_id, title, description, category_name, url):
    # 构建搜索文本
    search_text = f"{title} {description} {category_name}"
    
    # 生成向量（调用 Embedding API）
    vector = self.embedding_client.generate_embedding(search_text)
    # 例如："GitHub - 代码托管平台" → [0.234, -0.567, 0.890, ..., 0.345]
    
    # 存储到 Qdrant（向量 + 元数据）
    self.vector_store.store_vector(website_id, vector, payload)
```

**索引时也需要调用 Embedding API：**
- 将网站的标题、描述、分类转换为向量
- 存储到 Qdrant 中，供后续搜索使用

## 向量搜索的工作原理

### 向量相似度计算

```
查询向量: [0.123, -0.456, 0.789, ...]
网站1向量: [0.125, -0.458, 0.791, ...]  → 相似度: 0.95 (很相似)
网站2向量: [0.500, 0.200, -0.300, ...] → 相似度: 0.12 (不相似)
网站3向量: [0.130, -0.450, 0.785, ...] → 相似度: 0.88 (较相似)
```

**Qdrant 使用余弦相似度（Cosine Similarity）计算：**
- 值范围：0 到 1
- 1.0 = 完全相同
- 0.0 = 完全不同
- 通常阈值设为 0.3，只返回相似度 ≥ 0.3 的结果

## 为什么不能省略 Embedding API 调用？

### ❌ 错误理解
> "Qdrant 中已经存储了所有网站的向量，为什么还要调用 Embedding API？"

### ✅ 正确理解
> "Qdrant 中存储的是**网站**的向量，但用户的**查询**也需要转换为向量才能搜索"

### 类比理解

想象一个图书馆：
- **Qdrant** = 图书馆（存储所有书籍的"特征向量"）
- **网站向量** = 每本书的特征（已存储在图书馆）
- **查询向量** = 你想要找的书的关键词特征（需要实时生成）

**你不能说：**
> "图书馆里已经有书了，为什么还要告诉我你想要什么书？"

**正确的流程是：**
1. 你描述想要的书（用户输入查询）
2. 将你的描述转换为"特征"（调用 Embedding API）
3. 在图书馆中搜索匹配的书（Qdrant 向量搜索）
4. 返回最相关的书（相似度高的网站）

## 优化：向量缓存

为了减少 API 调用，我们实现了**查询向量缓存**：

```python
# app/utils/vector_service.py - EmbeddingClient.generate_embedding()

def generate_embedding(self, text: str, max_retries: int = 3, use_cache: bool = True):
    # 检查缓存
    if use_cache:
        cached_vector = get_cached_vector(text, self.model_name)
        if cached_vector:
            return cached_vector  # 直接返回，不调用 API
    
    # 缓存未命中，调用 API（带重试机制）
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=data, headers=headers, timeout=30)
            # 处理 503 等服务器错误，自动重试
            if response.status_code == 503:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # 递增等待：2s, 4s, 6s
                    time.sleep(wait_time)
                    continue
            embedding = response.json()['data'][0]['embedding']
            
            # 缓存结果
            cache_vector(text, self.model_name, embedding)
            
            return embedding
        except Exception as e:
            # 错误处理和重试逻辑...
            if attempt < max_retries - 1:
                continue
            raise
```

**缓存效果：**
- 相同查询（如"博客"）第二次搜索时，直接使用缓存的向量
- 不需要再次调用 Embedding API
- 大大减少 API 调用次数和延迟

**重试机制：**
- 默认最大重试 3 次
- 自动处理 503（服务不可用）、502、504 等服务器错误
- 处理网络超时（30秒超时）
- 使用指数退避策略（2s, 4s, 6s）
- 提高 API 调用的可靠性

## 总结

### Embedding API 调用的必要性

| 阶段 | 调用时机 | 作用 | 是否必需 |
|------|---------|------|---------|
| **索引阶段** | 添加/更新网站时 | 将网站文本转换为向量并存储 | ✅ 必需（只需一次） |
| **搜索阶段** | 每次用户搜索时 | 将用户查询转换为向量 | ✅ 必需（每次都需要） |

### 关键点

1. **Qdrant 只能搜索向量，不能搜索文本**
   - 必须先将文本转换为向量
   - 这是向量数据库的基本工作原理

2. **查询向量需要实时生成**
   - 用户的查询是动态的，无法预先存储
   - 每次搜索都需要调用 Embedding API（除非缓存命中）

3. **缓存可以优化性能**
   - 相同查询可以复用缓存的向量
   - 减少 API 调用和延迟

### 流程图

```
┌─────────────────────────────────────────────────────────┐
│  用户搜索："博客"                                        │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  检查缓存：是否有"博客"的向量？                          │
└──────────────────┬──────────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
        ▼ 缓存命中            ▼ 缓存未命中
┌──────────────┐    ┌──────────────────────────┐
│ 使用缓存向量 │    │ 调用 Embedding API       │
│ (不调用API)  │    │ "博客" → [向量]          │
└──────┬───────┘    └──────────┬───────────────┘
       │                      │
       │                      ▼
       │            ┌──────────────────────────┐
       │            │ 缓存向量（供下次使用）   │
       │            └──────────┬───────────────┘
       │                      │
       └──────────┬───────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│  在 Qdrant 中搜索相似向量                                │
│  查询向量 vs 所有网站向量 → 计算相似度                   │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  返回相似度 ≥ 0.3 的网站（按相似度排序）                 │
└─────────────────────────────────────────────────────────┘
```

## 结论

**Embedding API 调用是必需的，不能省略！**

- 它是向量搜索的核心步骤
- 将文本转换为向量是向量数据库工作的前提
- 通过缓存可以优化性能，但不能完全避免调用

