#!/bin/bash
# Video Hub + NarratoAI 统一部署；仅用 SSH 私钥（BatchMode，无密码交互）。
set -euo pipefail

SERVER_USER="${SERVER_USER:-root}"
SERVER_IP="${SERVER_IP:-39.105.112.189}"
REMOTE_DIR="/opt/video-hub"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519_grokbot}"
SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes
          -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)

if [[ ! -r "$SSH_KEY" ]]; then
  echo "缺少可用私钥：$SSH_KEY（可 export SSH_KEY=/path/to/key）" >&2
  exit 1
fi
# BatchMode=yes：服务器关掉密码登录后，缺 key 会立刻失败而不是卡在提示符
SSH_CMD=(ssh "${SSH_OPTS[@]}" "$SERVER_USER@$SERVER_IP")
RSYNC_SSH="ssh ${SSH_OPTS[*]}"

echo "=== [1/4] 本地测试与前端构建 ==="
# 后端跑在项目根目录 venv（systemd：ExecStart=$REMOTE_DIR/.venv/bin/python），backend/ 下没有 .venv
cd "$PROJECT_DIR/backend"
VIDEO_HUB_AUTH_MODE=off PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python" -m unittest discover -s tests -v
cd "$PROJECT_DIR/frontend"
npm run build

echo "=== [2/4] 同步代码（保留服务器运行数据） ==="
rsync -az --delete -e "$RSYNC_SSH" \
  --exclude='.git' --exclude='node_modules' --exclude='data' \
  --exclude='.env' --exclude='.venv' --exclude='backend/.venv' \
  --exclude='narratoai/.venv' --exclude='narratoai/.cache' \
  --exclude='narratoai/config.toml*' --exclude='narratoai/storage' \
  --exclude='narratoai/resource/videos' --exclude='narratoai/resource/scripts' \
  --exclude='narratoai/resource/bgm' --exclude='narratoai/resource/songs' \
  --exclude='narratoai/resource/srt' --exclude='narratoai/resource/fonts' \
  --exclude='narratoai/resource/safe_areas' --exclude='narratoai/resource/tts_reference_audio' \
  --exclude='services/.venv' --exclude='models' \
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
EOF

echo "=== 部署完成：http://$SERVER_IP ==="
