#!/bin/bash
# Video Hub 部署；仅用 SSH 私钥（BatchMode，无密码交互）。
# rsync 排除整个 narratoai/，避免 --delete 清掉服务器上的旧成片目录。
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
  --exclude='narratoai/' \
  --exclude='models' \
  --exclude='**/__pycache__' \
  "$PROJECT_DIR/" "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/"

echo "=== [3/4] 安装依赖、迁移配置并启动服务 ==="
# 公网 Host 传给远端安装脚本。guard 的 default_server 对 localhost 直接 444。
"${SSH_CMD[@]}" "REMOTE_DIR='$REMOTE_DIR' VIDEO_HUB_HEALTH_HOST='$SERVER_IP' bash '$REMOTE_DIR/deploy/remote-install.sh'"

echo "=== [4/4] 统一入口健康检查 ==="
# 打到本机 nginx，但 Host 必须是站点名。不带 Host 时 video-hub-guard 返回 444，curl -sf 会失败。
# 8000 端口的 uvicorn 不经过这层，远程安装脚本里那次检查保持直连。
"${SSH_CMD[@]}" bash -s -- "$SERVER_IP" <<'EOF'
set -e
health_host="$1"
for attempt in {1..30}; do
  if curl -sf -H "Host: ${health_host}" http://127.0.0.1/api/health; then
    echo
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "后端健康检查失败" >&2
    exit 1
  fi
  sleep 2
done
curl -sf -H "Host: ${health_host}" -o /dev/null -w 'Video Hub HTTP %{http_code}\n' http://127.0.0.1/
EOF

echo "=== 部署完成：http://$SERVER_IP ==="
