@echo off
REM Qdrant 停止脚本（Windows）

echo ========================================
echo 停止 Qdrant 向量数据库
echo ========================================

REM 查找 Qdrant 进程
tasklist | findstr /i qdrant >nul 2>&1
if %errorlevel% == 0 (
    echo [信息] 找到 Qdrant 进程，正在停止...
    taskkill /IM qdrant.exe /F >nul 2>&1
    if %errorlevel% == 0 (
        echo [成功] Qdrant 已停止
    ) else (
        echo [错误] 停止 Qdrant 失败，可能需要管理员权限
        echo 请以管理员身份运行此脚本
    )
) else (
    echo [信息] 未找到运行中的 Qdrant 进程
)

REM 检查端口是否释放
timeout /t 2 /nobreak >nul
netstat -ano | findstr :6333 >nul 2>&1
if %errorlevel% == 0 (
    echo [警告] 端口 6333 仍被占用
    echo 可能需要手动停止占用该端口的进程
) else (
    echo [信息] 端口 6333 已释放
)

echo ========================================
pause

