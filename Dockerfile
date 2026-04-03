# 第一阶段：构建依赖
FROM python:3.9-alpine AS builder

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ARG PIP_FALLBACK_INDEX_URL=https://pypi.org/simple
ARG PIP_TIMEOUT=180
ARG PIP_RETRIES=8

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=${PIP_TIMEOUT} \
    PIP_ROOT_USER_ACTION=ignore

# 设置工作目录
WORKDIR /app

# 切换Alpine镜像源为国内源
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories \
    && apk add --no-cache gcc musl-dev libffi-dev

# 配置pip镜像源与网络超时
RUN python -m pip install --upgrade pip setuptools wheel \
    && pip config set global.index-url "${PIP_INDEX_URL}" \
    && pip config set global.timeout "${PIP_TIMEOUT}" \
    && pip config set global.retries "${PIP_RETRIES}" \
    && if [ -n "${PIP_TRUSTED_HOST}" ]; then pip config set global.trusted-host "${PIP_TRUSTED_HOST}"; fi

# 复制依赖文件，安装到轮子目录
COPY requirements.txt .
RUN set -eux; \
    build_wheels() { \
        pip wheel --prefer-binary --no-cache-dir --wheel-dir /app/wheels -r requirements.txt; \
        pip wheel --prefer-binary --no-cache-dir --wheel-dir /app/wheels gunicorn; \
    }; \
    build_wheels || { \
        echo "Primary pip mirror failed, retrying with official PyPI..."; \
        pip config set global.index-url "${PIP_FALLBACK_INDEX_URL}"; \
        pip config unset global.trusted-host || true; \
        build_wheels; \
    }

# 第二阶段：运行环境
FROM python:3.9-alpine

# 设置工作目录和环境变量
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 切换Alpine镜像源并安装运行时依赖
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories \
    && apk add --no-cache nginx supervisor libffi tzdata \
    && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && mkdir -p /run/nginx /app/app/data /app/app/backups /app/app/static/uploads /app/app/static /data \
    && rm -rf /var/cache/apk/* \
    && rm -rf /tmp/*

# 从构建阶段复制wheel并安装
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels \
    && rm -rf /root/.cache/pip \
    && rm -rf /tmp/* \
    && find /usr/local/lib/python3.9 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true \
    && find /usr/local/lib/python3.9 -name "*.pyc" -delete 2>/dev/null || true \
    && find /usr/local/lib/python3.9 -name "*.pyo" -delete 2>/dev/null || true

# 配置Nginx
COPY docker/nginx.conf /defaults/nginx.conf

# 配置Supervisor
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# 添加启动脚本并设置执行权限
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/cleanup_backups.sh /app/docker/cleanup_backups.sh
RUN chmod +x /entrypoint.sh /app/docker/cleanup_backups.sh

# 复制应用代码（放在最后以利用缓存）
COPY . .

# 设置持久化卷
VOLUME ["/data", "/app/app/backups", "/app/app/static/uploads", "/etc/nginx/http.d"]

# 暴露端口
EXPOSE 80

# 使用启动脚本作为容器入口点
ENTRYPOINT ["/entrypoint.sh"] 
