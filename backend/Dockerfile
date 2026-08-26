# syntax=docker/dockerfile:1
# Video Hub 后端：FastAPI + yt-dlp + 内置发布引擎(sau CLI + patchright chromium)
# 构建上下文为项目根目录
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    VIDEO_HUB_PUBLISHER_DIR=/opt/publisher \
    VIDEO_HUB_DATA_DIR=/app/data \
    VIDEO_HUB_SAU_BIN=/opt/venv/bin/sau \
    VIDEO_HUB_COOKIES_DIR=/opt/publisher/cookies \
    VIDEO_HUB_XVFB=1

# ffmpeg: 视频探测/封面；xvfb+xauth: 抖音校验需要有头浏览器；curl: 健康检查
# apt 源换腾讯云内网镜像（deb.debian.org 从国内服务器访问极慢）
RUN sed -i 's|deb.debian.org|mirrors.cloud.tencent.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg xvfb xauth curl \
    && rm -rf /var/lib/apt/lists/*

# 统一 Python 环境：后端依赖 + 发布引擎
COPY backend/requirements.txt /tmp/requirements.txt
COPY publisher /opt/publisher
# 引擎的 qr_login.py / sau_cli.py 依赖 conf.py，仓库里只有模板，构建时自动生成
RUN cp -n /opt/publisher/conf.example.py /opt/publisher/conf.py \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt -e /opt/publisher

# Chromium 运行所需的系统库（浏览器本体通过 volume 挂载，见 docker-compose）
RUN /opt/venv/bin/patchright install-deps chromium

COPY backend /app/backend
WORKDIR /app/backend

EXPOSE 8000
CMD ["/opt/venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
