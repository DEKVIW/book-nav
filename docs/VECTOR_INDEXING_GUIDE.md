# 向量索引生成指南

## 概述

本文档说明如何为网站数据生成向量索引，包括：

1. **批量生成**：为数据库中所有现有网站生成向量
2. **自动触发**：添加/修改网站时自动生成向量

---

## 一、批量生成向量（适用于已有大量数据）

有两种方式可以批量生成向量：

1. **后台管理界面**（推荐，可视化操作，实时进度）
2. **命令行脚本**（适合自动化场景）

### 1.1 使用后台管理界面（推荐）

#### 操作步骤

1. 访问后台管理：http://localhost:5000/admin
2. 进入 **站点设置** → **AI 搜索设置**
3. 在 **向量搜索配置** 区域找到 **批量生成向量索引** 卡片
4. 配置选项：
   - ✅ **跳过已有向量**（默认）：只生成未生成向量的网站
   - ❌ **跳过已有向量**（取消勾选）：强制重新生成所有向量
5. 点击 **开始生成** 按钮
6. 实时查看：
   - 进度条
   - 统计信息（总数、已处理、成功、失败、跳过、耗时）

#### 功能特性

- ✅ **可视化界面**：实时查看进度和统计
- ✅ **可随时停止**：点击 **停止生成** 按钮
- ✅ **自动跳过已存在向量**（可选）
- ✅ **后台异步执行**：不阻塞其他操作
- ✅ **错误处理**：单个网站失败不影响整体

#### 注意事项

- 需要超级管理员权限
- 确保已配置 AI API 和 Qdrant
- 大量数据时可能需要较长时间，建议在低峰期执行

---

### 1.2 使用批量生成脚本

已创建 `batch_generate_vectors.py` 脚本，用于为所有现有网站生成向量。

#### 基本使用

```bash
# 在项目根目录执行
python batch_generate_vectors.py
```

#### 功能特性

- ✅ **自动跳过已存在的向量**（默认行为，避免重复生成）
- ✅ **显示详细进度**（每 10 个网站显示一次）
- ✅ **错误处理**（单个网站失败不影响整体）
- ✅ **统计信息**（成功/跳过/失败数量）

#### 高级选项

```bash
# 强制重新生成所有向量（不跳过已存在的）
python batch_generate_vectors.py --no-skip

# 自定义进度显示批次大小
python batch_generate_vectors.py --batch-size 20
```

#### 执行流程

```
1. 检查配置（API地址、密钥、模型、Qdrant地址）
2. 初始化向量服务
3. 获取所有网站
4. 检查已存在的向量（可选）
5. 遍历所有网站，生成向量
6. 显示统计信息
```

#### 输出示例

```
📋 配置信息：
   API地址: http://localhost:11434
   Embedding模型: bge-large-zh-v1.5
   Qdrant地址: http://localhost:6333

✅ 向量服务初始化成功（维度: 1024）

📊 找到 1000 个网站
🔍 检查已存在的向量...
   ✅ 发现 200 个网站已有向量，将跳过

🚀 开始生成向量...
============================================================
[10/1000] ✅ GitHub - 代码托管平台 - 向量生成成功
[20/1000] ✅ 博客园 - 程序员博客 - 向量生成成功
...
============================================================
📊 向量生成完成！
   ✅ 成功: 800
   ⏭️  跳过: 200
   ❌ 失败: 0
   📈 总计: 1000
============================================================
```

---

## 二、自动触发向量生成（已实现）

### 2.1 触发时机

以下操作会自动触发向量生成：

#### ✅ 添加网站时

1. **后台管理 - 添加网站** (`app/admin/websites.py`)

   ```python
   @bp.route('/website/add', methods=['GET', 'POST'])
   def add_website():
       # ... 添加网站 ...
       # 异步生成向量
       trigger_vector_indexing(website.id, category_name)
   ```

2. **API - 快速添加网站** (`app/main/api_website.py`)
   ```python
   @bp.route('/api/website/quick-add', methods=['POST'])
   def quick_add_website():
       # ... 添加网站 ...
       # 异步生成向量
       _trigger_vector_indexing(website.id, category_name)
   ```

#### ✅ 修改网站时

1. **后台管理 - 编辑网站** (`app/admin/websites.py`)

   ```python
   @bp.route('/website/edit/<int:id>', methods=['GET', 'POST'])
   def edit_website(id):
       # ... 更新网站 ...
       # 检查是否需要更新向量
       needs_vector_update = (
           old_title != website.title or
           old_description != website.description or
           old_category_id != website.category_id
       )
       if needs_vector_update:
           trigger_vector_indexing(website.id, new_category_name)
   ```

2. **API - 更新网站** (`app/main/api_website.py`)

   ```python
   @bp.route('/api/website/<int:site_id>/update', methods=['POST'])
   def update_website(site_id):
       # ... 更新网站 ...
       if needs_vector_update:
           _trigger_vector_indexing(website.id, new_category_name)
   ```

3. **API - 修改链接** (`app/main/api_website.py`)
   ```python
   @bp.route('/api/modify_link', methods=['POST'])
   def api_modify_link():
       # ... 修改链接 ...
       if needs_vector_update:
           _trigger_vector_indexing(website.id, category_name)
   ```

### 2.2 触发条件

向量生成只在以下情况触发：

- ✅ **标题变化**
- ✅ **描述变化**
- ✅ **分类变化**

**不会触发的情况：**

- ❌ 仅修改 URL（不影响搜索内容）
- ❌ 仅修改图标
- ❌ 仅修改排序权重
- ❌ 仅修改私有/推荐状态

### 2.3 实现机制

#### 异步后台执行

向量生成在**后台线程**中执行，不会阻塞主流程：

```python
def _trigger_vector_indexing(website_id: int, category_name: str = None):
    """异步触发向量生成（后台线程执行，不阻塞主流程）"""
    def _generate_vector_in_background():
        # 在后台线程中执行向量生成
        with current_app.app_context():
            # ... 生成向量 ...

    # 启动后台线程
    thread = threading.Thread(target=_generate_vector_in_background, daemon=True)
    thread.start()
```

#### 配置检查

向量生成前会检查配置是否完整：

```python
# 检查向量搜索是否启用
if not (settings and settings.vector_search_enabled and
        all([settings.qdrant_url, settings.embedding_model,
             settings.ai_api_base_url, settings.ai_api_key])):
    return  # 配置不完整，不生成向量
```

---

## 三、使用场景

### 场景 1：新部署项目（数据库已有数据）

**方法 A：使用后台管理界面（推荐）**

```bash
# 1. 配置AI搜索和向量搜索（后台管理 → 站点设置）
# 2. 在后台管理界面点击"批量生成向量索引" → "开始生成"
# 3. 后续添加/修改网站会自动生成向量
```

**方法 B：使用命令行脚本**

```bash
# 1. 配置AI搜索和向量搜索（后台管理 → 站点设置）
# 2. 运行批量生成脚本
python batch_generate_vectors.py

# 3. 后续添加/修改网站会自动生成向量
```

### 场景 2：从其他系统迁移数据

**方法 A：使用后台管理界面（推荐）**

```bash
# 1. 导入数据（后台管理 → 数据管理）
# 2. 在后台管理界面点击"批量生成向量索引" → "开始生成"
# 3. 后续操作自动触发
```

**方法 B：使用命令行脚本**

```bash
# 1. 导入数据（后台管理 → 数据管理）
# 2. 运行批量生成脚本
python batch_generate_vectors.py

# 3. 后续操作自动触发
```

### 场景 3：更换 Embedding 模型

**方法 A：使用后台管理界面（推荐）**

```bash
# 1. 在后台管理修改Embedding模型
# 2. 在后台管理界面取消勾选"跳过已有向量"，点击"开始生成"
```

**方法 B：使用命令行脚本**

```bash
# 1. 在后台管理修改Embedding模型
# 2. 重新生成所有向量（强制模式）
python batch_generate_vectors.py --no-skip
```

### 场景 4：日常使用

- ✅ 添加网站 → 自动生成向量
- ✅ 修改网站标题/描述/分类 → 自动更新向量
- ✅ 无需手动操作

---

## 四、注意事项

### 4.1 性能考虑

- **批量生成**：大量数据时可能需要较长时间

  - 1000 个网站 ≈ 10-30 分钟（取决于 API 速度）
  - 建议在低峰期执行

- **自动触发**：后台异步执行，不影响用户体验
  - 单个向量生成：200-500ms
  - 不阻塞网站添加/修改操作

### 4.2 错误处理

- **批量生成**：单个网站失败不影响其他网站
- **自动触发**：失败只记录日志，不影响主流程
- **查看日志**：检查 `current_app.logger` 输出

### 4.3 配置要求

向量生成需要以下配置：

- ✅ 向量搜索已启用
- ✅ Qdrant URL 已配置
- ✅ Embedding 模型已配置
- ✅ AI API 地址和密钥已配置

---

## 五、代码位置总结

### 批量生成功能

- **后台管理界面**：`app/admin/vector_indexing.py` - 批量生成向量路由和状态 API
- **命令行脚本**：`batch_generate_vectors.py` - 批量生成脚本

### 自动触发实现

- `app/main/api_website.py` - API 接口的向量触发

  - `quick_add_website()` - 快速添加
  - `update_website()` - 更新网站
  - `api_modify_link()` - 修改链接
  - `_trigger_vector_indexing()` - 触发函数

- `app/admin/websites.py` - 后台管理的向量触发

  - `add_website()` - 添加网站
  - `edit_website()` - 编辑网站

- `app/admin/utils.py` - 工具函数
  - `trigger_vector_indexing()` - 触发函数（后台管理使用）

### 向量服务

- `app/utils/vector_service.py` - 向量服务核心
  - `EmbeddingClient` - Embedding API 客户端
  - `QdrantVectorStore` - Qdrant 存储客户端
  - `VectorSearchService` - 向量搜索服务

---

## 六、常见问题

### Q1: 批量生成时如何查看进度？

A:

- **后台管理界面**：实时显示进度条、统计信息和耗时
- **命令行脚本**：每 10 个网站显示一次进度，也可以使用 `--batch-size` 参数调整显示频率

### Q2: 批量生成中断了怎么办？

A: 脚本默认跳过已存在的向量，重新运行会继续处理未生成的网站。

### Q3: 如何强制重新生成所有向量？

A: 使用 `--no-skip` 参数：

```bash
python batch_generate_vectors.py --no-skip
```

### Q4: 自动触发失败怎么办？

A: 检查日志文件，确认：

- API 配置是否正确
- Qdrant 服务是否运行
- 网络连接是否正常

### Q5: 如何验证向量是否生成成功？

A: 可以通过搜索功能测试，如果向量搜索返回结果，说明向量已生成。

---

## 七、总结

### ✅ 已实现的功能

1. **批量生成脚本** - 为所有现有网站生成向量
2. **自动触发机制** - 添加/修改网站时自动生成向量
3. **智能判断** - 只在内容变化时触发
4. **异步执行** - 不阻塞主流程
5. **错误处理** - 完善的异常处理机制

### 📝 使用建议

1. **首次部署**：运行批量生成脚本
2. **日常使用**：依赖自动触发机制
3. **模型更换**：使用 `--no-skip` 重新生成
4. **监控日志**：定期检查错误日志
