# Windows 快速开始指南（不使用 Docker）

## 🚀 快速步骤

### 1. 安装 Python 依赖

```powershell
# 激活虚拟环境
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 下载并启动 Qdrant

#### 2.1 下载 Qdrant

1. 访问：https://github.com/qdrant/qdrant/releases
2. 下载最新版本的 Windows 版本（例如：`qdrant-x.x.x-windows-amd64.exe`）
3. 解压到 `C:\tools\qdrant\`（或任意目录）

#### 2.2 启动 Qdrant

**方法 A：使用提供的批处理脚本（推荐）**

1. 编辑 `start_qdrant.bat`，修改 `QDRANT_PATH` 为你的 Qdrant 路径
2. 双击运行 `start_qdrant.bat`

**方法 B：手动启动**

```powershell
# 在 PowerShell 中
cd C:\tools\qdrant
.\qdrant.exe
```

**验证 Qdrant 是否运行：**

打开浏览器访问：http://localhost:6333/dashboard

如果能看到 Qdrant 管理界面，说明运行成功。

### 3. 更新数据库

```powershell
python init_db.py
# 选择选项 2：更新数据库结构
```

### 4. 配置向量搜索

1. 启动应用：
   ```powershell
   python run.py
   ```

2. 访问后台：http://localhost:5000/admin

3. 进入 **站点设置** → **AI搜索设置**

4. 配置：
   - ✅ 启用向量搜索
   - Qdrant 地址：`http://localhost:6333`
   - Embedding 模型：`text-embedding-3-small`
   - 相似度阈值：`0.3`
   - 填写 AI API 配置（基础 URL 和密钥）

5. 保存

### 5. 为网站生成向量

```powershell
python test_vector_indexing.py
```

### 6. 测试搜索

在前端启用 AI 搜索开关，或运行：

```powershell
python test_vector_search.py
```

## 📝 重要提示

1. **Qdrant 必须保持运行**：启动 Qdrant 后不要关闭命令行窗口
2. **端口占用**：如果 6333 端口被占用，使用 `netstat -ano | findstr :6333` 查看并停止占用进程
3. **防火墙**：首次运行时 Windows 防火墙可能会提示，需要允许 Qdrant 访问网络

## 🔧 常用命令

```powershell
# 检查 Qdrant 是否运行
Invoke-WebRequest -Uri http://localhost:6333/health -UseBasicParsing

# 停止 Qdrant（如果使用批处理脚本启动）
.\stop_qdrant.bat

# 查看端口占用
netstat -ano | findstr :6333
```

## ❓ 遇到问题？

查看完整指南：`LOCAL_DEVELOPMENT_GUIDE.md`

