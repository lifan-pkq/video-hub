#!/bin/bash
# Video Hub 部署脚本
# 用法: SSHPASS='服务器密码' ./deploy.sh
set -e

SERVER_USER="ubuntu"
SERVER_IP="49.234.25.175"
REMOTE_DIR="/home/ubuntu/video-hub"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

: "${SSHPASS:?请先设置环境变量 SSHPASS=服务器SSH密码}"

SSH_CMD=(sshpass -p "$SSHPASS" ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP")
RSYNC_SSH="sshpass -p $SSHPASS ssh -o StrictHostKeyChecking=no"

echo "=== [1/6] 本地构建前端 ==="
cd "$PROJECT_DIR/frontend"
npm run build

echo "=== [2/6] 同步项目代码到服务器 ==="
rsync -avz --delete -e "$RSYNC_SSH" \
  --exclude='.git' --exclude='node_modules' --exclude='data' \
  --exclude='.env' \
  --exclude='publisher/.venv' --exclude='publisher/logs' \
  --exclude='**/__pycache__' --exclude='ms-playwright' \
  "$PROJECT_DIR/" "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/"

echo "=== [3/6] 服务器端下载 Chromium 浏览器（npmmirror 镜像，约 290MB） ==="
"${SSH_CMD[@]}" bash -s <<'EOF'
set -e
PW=/home/ubuntu/video-hub/ms-playwright
mkdir -p $PW/chromium-1208 $PW/chromium_headless_shell-1208 $PW/ffmpeg-1011
CFT=https://cdn.npmmirror.com/binaries/chrome-for-testing/145.0.7632.6/linux64
if [ ! -f $PW/chromium-1208/INSTALLATION_COMPLETE ]; then
  curl -sfL $CFT/chrome-linux64.zip -o /tmp/chrome.zip && unzip -qo /tmp/chrome.zip -d $PW/chromium-1208/ && rm /tmp/chrome.zip
  chmod +x $PW/chromium-1208/chrome-linux64/chrome && touch $PW/chromium-1208/INSTALLATION_COMPLETE
fi
if [ ! -f $PW/chromium_headless_shell-1208/INSTALLATION_COMPLETE ]; then
  curl -sfL $CFT/chrome-headless-shell-linux64.zip -o /tmp/shell.zip && unzip -qo /tmp/shell.zip -d $PW/chromium_headless_shell-1208/ && rm /tmp/shell.zip
  chmod +x $PW/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell && touch $PW/chromium_headless_shell-1208/INSTALLATION_COMPLETE
fi
if [ ! -f $PW/ffmpeg-1011/INSTALLATION_COMPLETE ]; then
  curl -sfL https://cdn.npmmirror.com/binaries/playwright/builds/ffmpeg/1011/ffmpeg-linux.zip -o /tmp/ffmpeg.zip && unzip -qo /tmp/ffmpeg.zip -d $PW/ffmpeg-1011/ && rm /tmp/ffmpeg.zip
  chmod +x $PW/ffmpeg-1011/ffmpeg-linux && touch $PW/ffmpeg-1011/INSTALLATION_COMPLETE
fi
echo "Chromium 组件就绪"
EOF

echo "=== [4/6] 停止并备份服务器上的旧项目 ==="
"${SSH_CMD[@]}" bash -s <<'EOF'
set -e
# 停止旧项目容器
docker rm -f douyin-auto-web douyin-auto-scheduler 2>/dev/null || true
# 备份旧项目目录后删除（data/ 下有 root 所有的文件，需要 sudo）
if [ -d /home/ubuntu/douyin-auto-system ]; then
  ts=$(date +%Y%m%d%H%M%S)
  sudo tar -czf "/home/ubuntu/douyin-auto-system.bak.$ts.tar.gz" -C /home/ubuntu douyin-auto-system
  sudo chown ubuntu:ubuntu "/home/ubuntu/douyin-auto-system.bak.$ts.tar.gz"
  sudo rm -rf /home/ubuntu/douyin-auto-system
  echo "旧项目已备份到 /home/ubuntu/douyin-auto-system.bak.$ts.tar.gz 并删除"
fi
# 清理旧镜像
docker rmi deploy-web deploy-scheduler 2>/dev/null || true
# 释放 80 端口可能残留的其他容器
docker ps --filter "publish=80" -q | xargs -r docker stop 2>/dev/null || true
EOF

echo "=== [5/6] 服务器上构建并启动 ==="
"${SSH_CMD[@]}" "cd $REMOTE_DIR && docker compose build && docker compose up -d"

echo "=== [6/6] 健康检查 ==="
sleep 8
"${SSH_CMD[@]}" "curl -sf http://localhost/api/health && echo && curl -s -o /dev/null -w '前端 HTTP %{http_code}\n' http://localhost/"

echo ""
echo "=== 部署完成 ==="
echo "访问地址: http://$SERVER_IP"
