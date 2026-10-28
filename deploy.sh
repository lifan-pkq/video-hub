#!/bin/bash
# Video Hub + NarratoAI 统一部署；密码只从 SSHPASS 环境变量读取。
set -euo pipefail

SERVER_USER="${SERVER_USER:-root}"
SERVER_IP="${SERVER_IP:-39.105.112.189}"
REMOTE_DIR="/opt/video-hub"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

: "${SSHPASS:?请先设置环境变量 SSHPASS=服务器SSH密码}"

SSH_CMD=(sshpass -e ssh -o StrictHostKeyChecking=accept-new "$SERVER_USER@$SERVER_IP")
RSYNC_SSH="sshpass -e ssh -o StrictHostKeyChecking=accept-new"

echo "=== [1/4] 本地测试与前端构建 ==="
cd "$PROJECT_DIR/backend"
.venv/bin/python -m unittest discover -s tests -v
cd "$PROJECT_DIR/frontend"
npm run build

echo "=== [2/4] 同步代码（保留服务器运行数据） ==="
rsync -az --delete -e "$RSYNC_SSH" \
  --exclude='.git' --exclude='node_modules' --exclude='data' \
  --exclude='.env' --exclude='.venv' --exclude='backend/.venv' \
  --exclude='publisher/.venv' --exclude='publisher/logs' \
  --exclude='publisher/cookies' --exclude='publisher/db' --exclude='publisher/media' \
  --exclude='publisher/conf.py' \
  --exclude='narratoai/.venv' --exclude='narratoai/.cache' \
  --exclude='narratoai/config.toml*' --exclude='narratoai/storage' \
  --exclude='narratoai/resource/videos' --exclude='narratoai/resource/scripts' \
  --exclude='narratoai/resource/bgm' --exclude='narratoai/resource/songs' \
  --exclude='narratoai/resource/srt' --exclude='narratoai/resource/fonts' \
  --exclude='narratoai/resource/safe_areas' \
  --exclude='services/.venv' --exclude='models' --exclude='ms-playwright' \
  --exclude='**/access_token.txt' --exclude='**/sms_token.txt' --exclude='**/verify_code.txt' \
  --exclude='**/__pycache__' \
  "$PROJECT_DIR/" "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/"

echo "=== [3/4] 安装依赖、迁移配置并启动服务 ==="
"${SSH_CMD[@]}" "REMOTE_DIR='$REMOTE_DIR' bash '$REMOTE_DIR/deploy/remote-install.sh'"

echo "=== [4/4] 统一入口健康检查 ==="
"${SSH_CMD[@]}" bash -s <<'EOF'
set -e
for attempt in {1..30}; do
  if curl -sf http://localhost/api/health; then
    echo
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "后端健康检查失败" >&2
    exit 1
  fi
  sleep 2
done
curl -sf -o /dev/null -w 'Video Hub HTTP %{http_code}\n' http://localhost/
curl -sf http://localhost/narrato/_stcore/health
curl -sf http://localhost/preview/health
EOF

echo "=== 部署完成：http://$SERVER_IP ==="
