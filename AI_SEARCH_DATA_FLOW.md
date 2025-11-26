# AI智能搜索数据传输流程详解

## 整体架构

```
用户输入 → 前端处理 → 后端API → 并行搜索 → 流式返回 → 前端渲染
```

## 详细流程

### 1. 前端发起搜索（`app/static/js/search.js`）

#### 1.1 用户操作
- 用户在搜索框输入查询关键词
- 可选择启用/关闭AI搜索（通过 `aiSearchToggle` 开关）
- 提交搜索表单

#### 1.2 构建请求URL
```javascript
// 基础URL
let searchUrl = `/api/search?q=${encodeURIComponent(query)}`;

// 如果启用AI搜索，添加参数
if (useAI) {
    searchUrl += "&ai=true&progressive=true";  // 启用渐进式返回
}
```

#### 1.3 选择搜索方式
- **AI搜索（渐进式）**：使用 `EventSource` 建立SSE连接
- **传统搜索**：使用 `fetch` 一次性获取结果

---

### 2. 后端接收请求（`app/main/api_search.py`）

#### 2.1 路由处理
```python
@bp.route('/api/search')
def api_search():
    query = request.args.get('q', '')
    use_ai = request.args.get('ai', 'false').lower() == 'true'
    progressive = request.args.get('progressive', 'false').lower() == 'true'
```

#### 2.2 路由决策
- 如果 `progressive=true` 且 `use_ai=true` → 调用 `_progressive_search()`（SSE流式返回）
- 如果 `use_ai=true` 但 `progressive=false` → 执行AI搜索但一次性返回JSON
- 否则 → 执行传统关键词搜索

---

### 3. 渐进式搜索流程（`_progressive_search`）

#### 3.1 阶段1：关键词搜索（立即返回）
```python
# 立即执行关键词搜索，不等待其他任务
keyword_query = base_query.filter(
    or_(
        Website.title.ilike(f'%{query}%'),
        Website.description.ilike(f'%{query}%'),
        Website.url.ilike(f'%{query}%')
    )
)
keyword_results = keyword_query.limit(20).all()

# 立即发送第一阶段结果
yield f"data: {json.dumps({
    'stage': 'initial',
    'websites': websites_data,
    'total': len(websites_data),
    'status': '关键词搜索结果已返回，正在补充向量搜索结果...'
})}\n\n"
sys.stdout.flush()  # 确保立即发送
```

**前端处理**：
- 接收到 `stage: 'initial'` 事件
- 立即渲染关键词搜索结果
- 显示状态提示："正在补充向量搜索结果..."

#### 3.2 阶段2：向量搜索（分批返回）
```python
if settings.vector_search_enabled:
    # 1. 生成查询向量
    embedding_client = EmbeddingClient(...)
    query_embedding = embedding_client.generate_embedding(query)
    
    # 2. 在Qdrant中搜索相似向量
    vector_search_results = vector_service.search(
        query=query,
        limit=50,
        threshold=0.3
    )
    
    # 3. 分批返回结果（每批15条）
    batch_size = 15
    for batch_idx in range(total_batches):
        end_idx = min((batch_idx + 1) * batch_size, len(vector_websites))
        all_websites = vector_websites[:end_idx] + initial_keyword_results
        
        yield f"data: {json.dumps({
            'stage': 'enhanced',
            'websites': all_websites,  # 累积结果
            'total': len(all_websites),
            'status': f'向量搜索结果已补充 ({end_idx}/{len(vector_websites)})...'
        })}\n\n"
        
        if batch_idx < total_batches - 1:
            time.sleep(0.15)  # 150ms延迟，实现渐进效果
```

**前端处理**：
- 接收到 `stage: 'enhanced'` 事件
- 检查是否有新网站（通过ID对比）
- 如果有新网站，使用 `_appendWebsites()` 增量添加
- 如果没有新网站但总数增加，重新渲染以更新排序

#### 3.3 阶段3：AI智能排序（最终返回）
```python
if settings.ai_search_enabled:
    # 1. AI意图理解（如果需要）
    if needs_ai_intent:
        intent = ai_service.analyze_search_intent(query)
    
    # 2. AI推荐排序
    recommendations = ai_service.recommend_websites(
        query,
        intent,
        websites_for_ai[:50],  # 最多50个网站给AI排序
        vector_scores=vector_scores,
        max_recommendations=20
    )
    
    # 3. 按AI推荐顺序重新排序
    recommended_ids = [rec['website_id'] for rec in recommendations['recommendations']]
    ai_sorted_websites = [website_id_map[wid] for wid in recommended_ids]
    
    # 4. 发送最终结果
    yield f"data: {json.dumps({
        'stage': 'final',
        'websites': ai_sorted_websites,
        'total': len(ai_sorted_websites),
        'ai_enabled': True,
        'ai_summary': recommendations.get('summary', ''),
        'status': 'AI智能排序完成'
    })}\n\n"
```

**前端处理**：
- 接收到 `stage: 'final'` 事件
- 检查结果顺序是否改变
- 如果顺序改变，重新渲染（AI排序后的顺序）
- 如果顺序未变，只更新状态提示和AI摘要
- 关闭EventSource连接

---

### 4. 非渐进式AI搜索流程

#### 4.1 并行执行三个任务
```python
with ThreadPoolExecutor(max_workers=3) as executor:
    # 任务1：向量搜索
    future_vector = executor.submit(do_vector_search)
    
    # 任务2：关键词搜索
    future_keyword = executor.submit(do_keyword_search)
    
    # 任务3：AI意图理解（如果需要）
    future_intent = executor.submit(do_ai_intent) if needs_ai_intent else None
    
    # 等待所有任务完成
    vector_ids, vector_scores = future_vector.result()
    keyword_results = future_keyword.result()
    if future_intent:
        intent = future_intent.result()
```

#### 4.2 合并和扩展搜索结果
```python
# 合并向量搜索和关键词搜索结果
candidate_sites.update(vector_ids)
for site in keyword_results:
    candidate_sites.add(site.id)

# 根据AI意图扩展搜索（使用关键词和相关词）
if intent.get('keywords'):
    for keyword in expanded_keywords[:5]:
        # 扩展搜索...
```

#### 4.3 AI推荐排序
```python
recommendations = ai_service.recommend_websites(
    query, 
    intent, 
    websites_for_ai,
    vector_scores=vector_scores,
    max_recommendations=20
)

# 按AI推荐顺序排序
ai_results = [website_id_map[wid] for wid in recommended_ids]
```

#### 4.4 一次性返回JSON
```python
return jsonify({
    "websites": websites_data,
    "ai_enabled": True,
    "ai_summary": ai_summary,
    "total": len(websites_data)
})
```

---

### 5. AI服务调用（`app/utils/ai_search.py`）

#### 5.1 意图分析
```python
def analyze_search_intent(self, user_query: str) -> dict:
    # 调用AI API
    response = self._call_api(messages, max_tokens=300)
    
    # 返回格式：
    # {
    #     "intent": "用户想要查找与'博客'相关的网站",
    #     "keywords": ["博客", "写作", "文章"],
    #     "related_terms": ["个人网站", "内容创作"],
    #     "category_hints": ["技术", "写作"],
    #     "search_type": "semantic"
    # }
```

#### 5.2 网站推荐
```python
def recommend_websites(self, user_query, intent, websites, vector_scores, max_recommendations):
    # 构建包含所有网站信息的prompt
    prompt = AI_SEARCH_RECOMMEND_PROMPT_TEMPLATE.format(
        user_query=user_query,
        intent=intent.get('intent', ''),
        total_count=len(websites),
        websites_list=json.dumps(websites_for_ai, ensure_ascii=False),
        max_recommendations=max_recommendations
    )
    
    # 调用AI API
    response = self._call_api(messages, max_tokens=2000)
    
    # 返回格式：
    # {
    #     "recommendations": [
    #         {
    #             "website_id": 1,
    #             "relevance_score": 0.95,
    #             "reason": "推荐理由"
    #         }
    #     ],
    #     "summary": "搜索总结"
    # }
```

---

### 6. 向量搜索流程（`app/utils/vector_service.py`）

#### 6.1 生成查询向量
```python
# 调用Embedding API
embedding = embedding_client.generate_embedding(query)
# 返回：1536维或1024维的浮点数向量
```

#### 6.2 Qdrant向量搜索
```python
# 在Qdrant中搜索相似向量
results = vector_store.client.search(
    collection_name="websites",
    query_vector=embedding,
    limit=50,
    score_threshold=0.3
)

# 返回：[
#     {
#         'id': 'website_123',
#         'score': 0.85,
#         'payload': {'website_id': 123}
#     }
# ]
```

---

### 7. 前端渲染（`app/static/js/search.js`）

#### 7.1 初始阶段渲染
```javascript
if (data.stage === "initial") {
    if (currentWebsites.length > 0) {
        _renderWebsites(currentWebsites);  // 完整渲染
    } else {
        // 显示加载提示
        resultsContent.innerHTML = `<div class="spinner-border">...</div>`;
    }
}
```

#### 7.2 增强阶段增量渲染
```javascript
else if (data.stage === "enhanced") {
    if (!hasExistingResults && currentWebsites.length > 0) {
        _renderWebsites(currentWebsites);  // 首次渲染
    } else if (hasExistingResults) {
        const newWebsites = currentWebsites.filter(
            (site) => !existingIds.has(site.id)
        );
        if (newWebsites.length > 0) {
            _appendWebsites(newWebsites);  // 增量添加（带动画）
        }
    }
}
```

#### 7.3 最终阶段智能更新
```javascript
else if (data.stage === "final") {
    const currentIds = Array.from(resultsContent.querySelectorAll(".site-card"))
        .map((card) => parseInt(card.dataset.id));
    const newIds = currentWebsites.map((site) => site.id);
    
    // 如果顺序改变，重新渲染
    if (JSON.stringify(currentIds) !== JSON.stringify(newIds)) {
        _renderWebsites(currentWebsites);  // AI排序后的结果
    } else {
        // 只更新状态提示
        updateSearchSummary(data);
    }
}
```

---

## 数据流向图

```
┌─────────────┐
│  用户输入   │
│  "博客"     │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────┐
│  前端：构建请求URL               │
│  /api/search?q=博客&ai=true     │
│  &progressive=true              │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│  后端：api_search()              │
│  - 检查缓存                      │
│  - 判断是否渐进式搜索            │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│  阶段1：关键词搜索（立即返回）   │
│  - SQL查询：title/description    │
│  - 返回前20条结果                │
│  - SSE发送：stage='initial'     │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│  前端：立即渲染关键词结果        │
│  - 显示前20条网站                │
│  - 显示状态："正在补充..."       │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│  阶段2：向量搜索（分批返回）     │
│  - 生成查询向量（Embedding API）│
│  - Qdrant向量搜索                │
│  - 分批返回（每批15条）          │
│  - SSE发送：stage='enhanced'    │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│  前端：增量渲染向量结果          │
│  - 检测新网站ID                  │
│  - 增量添加新网站（动画效果）    │
│  - 更新状态提示                  │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│  阶段3：AI智能排序（最终返回）   │
│  - AI意图分析（Chat API）        │
│  - AI推荐排序（Chat API）        │
│  - 按AI推荐顺序重新排序          │
│  - SSE发送：stage='final'        │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│  前端：最终渲染                  │
│  - 检查顺序是否改变              │
│  - 如果改变，重新渲染            │
│  - 显示AI摘要                    │
│  - 关闭EventSource连接           │
└─────────────────────────────────┘
```

---

## 关键优化点

### 1. 渐进式返回
- **阶段1**：立即返回关键词搜索结果（最快，<100ms）
- **阶段2**：分批返回向量搜索结果（每批150ms间隔）
- **阶段3**：返回AI排序后的最终结果

### 2. 并行处理
- 向量搜索、关键词搜索、AI意图分析并行执行
- 使用 `ThreadPoolExecutor` 提高效率

### 3. 智能缓存
- 短查询（≤5字符）启用缓存
- 缓存键：`query + use_ai + user_id`
- 缓存TTL：3600秒

### 4. 增量渲染
- 前端检测新网站ID，只添加新内容
- 使用CSS动画实现平滑过渡
- 避免不必要的DOM重绘

### 5. 错误处理
- 每个阶段都有异常捕获
- 失败时自动降级到传统搜索
- 前端有超时和错误回退机制

---

## API调用统计

### 一次完整AI搜索的API调用：

1. **Embedding API**（向量搜索时）：
   - 调用次数：1次（生成查询向量）
   - 路径：`POST /v1/embeddings`
   - 数据：`{"model": "bge-large-zh-v1.5", "input": "博客"}`

2. **Chat API**（AI排序时）：
   - 调用次数：1-2次
     - 意图分析：1次（如果需要）
     - 网站推荐：1次
   - 路径：`POST /v1/chat/completions`
   - 数据：包含用户查询和网站列表的prompt

3. **Qdrant API**（向量搜索时）：
   - 调用次数：1次
   - 路径：`POST /collections/websites/points/search`
   - 数据：查询向量和相似度阈值

---

## 性能指标

- **关键词搜索**：<100ms（数据库查询）
- **向量搜索**：200-500ms（包含Embedding API调用）
- **AI排序**：500-2000ms（取决于网站数量和AI响应速度）
- **总耗时（渐进式）**：
  - 第一阶段：<100ms（用户立即看到结果）
  - 第二阶段：200-500ms（增量补充）
  - 第三阶段：500-2000ms（AI优化排序）

---

## 总结

AI智能搜索采用**渐进式流式返回**架构，实现了：
1. **快速响应**：关键词结果立即返回
2. **逐步增强**：向量结果分批补充
3. **智能排序**：AI最终优化排序
4. **良好体验**：用户看到结果逐步加载，不会长时间等待

