@echo off
REM Qdrant 启动脚本（Windows）
REM 请根据你的实际路径修改 QDRANT_PATH

set QDRANT_PATH=C:\tools\qdrant\qdrant.exe
set QDRANT_DIR=C:\tools\qdrant

echo ========================================
echo 启动 Qdrant 向量数据库
echo ========================================

REM 检查 Qdrant 是否已经在运行
netstat -ano | findstr :6333 >nul 2>&1
if %errorlevel% == 0 (
    echo [警告] 端口 6333 已被占用，Qdrant 可能已在运行
    echo 正在检查进程...
    tasklist | findstr /i qdrant
    echo.
    echo 如果 Qdrant 已在运行，请忽略此警告
    echo 如果端口被其他程序占用，请先停止该程序
    pause
    exit /b
)

REM 检查 Qdrant 可执行文件是否存在
if not exist "%QDRANT_PATH%" (
    echo [错误] 找不到 Qdrant 可执行文件: %QDRANT_PATH%
    echo.
    echo 请执行以下步骤：
    echo 1. 访问 https://github.com/qdrant/qdrant/releases
    echo 2. 下载 Windows 版本的 Qdrant
    echo 3. 解压到 %QDRANT_DIR%
    echo 4. 或者修改此脚本中的 QDRANT_PATH 变量
    pause
    exit /b 1
)

REM 切换到 Qdrant 目录
cd /d "%QDRANT_DIR%"

echo [信息] 启动 Qdrant...
echo [信息] 数据存储路径: %QDRANT_DIR%\storage
echo [信息] HTTP API: http://localhost:6333
echo [信息] gRPC API: http://localhost:6334
echo [信息] 管理界面: http://localhost:6333/dashboard
echo.
echo 按 Ctrl+C 停止 Qdrant
echo ========================================
echo.

REM 启动 Qdrant（前台运行）
"%QDRANT_PATH%"

pause

