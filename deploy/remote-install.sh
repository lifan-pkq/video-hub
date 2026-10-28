#!/bin/bash
set -euo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/video-hub}"
BACKUP_DIR="/opt/video-hub-backups/$(date +%Y%m%d%H%M%S)"
export DEBIAN_FRONTEND=noninteractive

if [ "$(id -u)" -ne 0 ]; then
  echo "请使用 root 执行远端安装" >&2
  exit 1
fi

install -d -m 700 "$BACKUP_DIR"
for file in \
  /etc/nginx/sites-enabled/narratoai \
  /etc/systemd/system/narratoai.service \
  /etc/systemd/system/narrato-preview.service \
  /etc/systemd/system/narrato-asr.service \
  /opt/NarratoAI/config.toml; do
  if [ -f "$file" ]; then
    cp -a --parents "$file" "$BACKUP_DIR/"
  fi
done
echo "配置备份：$BACKUP_DIR"

apt-get update
apt-get install -y --no-install-recommends python3-venv python3-pip ffmpeg xvfb xauth curl rsync unzip

if ! id -u narratoai >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$REMOTE_DIR" --shell /usr/sbin/nologin narratoai
fi

mkdir -p "$REMOTE_DIR"/{data,models,ms-playwright}
mkdir -p "$REMOTE_DIR/publisher"/{cookies,db,logs,media}
mkdir -p "$REMOTE_DIR/narratoai"

if [ ! -f "$REMOTE_DIR/narratoai/config.toml" ]; then
  if [ -f /opt/NarratoAI/config.toml ]; then
    cp -a /opt/NarratoAI/config.toml "$REMOTE_DIR/narratoai/config.toml"
  else
    cp "$REMOTE_DIR/narratoai/config.example.toml" "$REMOTE_DIR/narratoai/config.toml"
  fi
fi
if [ ! -d "$REMOTE_DIR/narratoai/storage" ] && [ -d /opt/NarratoAI/storage ]; then
  cp -a /opt/NarratoAI/storage "$REMOTE_DIR/narratoai/storage"
fi
mkdir -p "$REMOTE_DIR/narratoai/storage"/{temp,tasks,json,narration_scripts,drama_analysis}
for directory in videos scripts; do
  if [ -d "/opt/NarratoAI/resource/$directory" ]; then
    mkdir -p "$REMOTE_DIR/narratoai/resource/$directory"
    cp -an "/opt/NarratoAI/resource/$directory/." "$REMOTE_DIR/narratoai/resource/$directory/"
  fi
done
if [ ! -f "$REMOTE_DIR/publisher/conf.py" ]; then
  cp "$REMOTE_DIR/publisher/conf.example.py" "$REMOTE_DIR/publisher/conf.py"
fi
if [ ! -f "$REMOTE_DIR/models/faster-whisper-small/model.bin" ] && [ -f /opt/narrato-services/models/faster-whisper-small/model.bin ]; then
  cp -a /opt/narrato-services/models/faster-whisper-small "$REMOTE_DIR/models/"
fi

python3 -m venv "$REMOTE_DIR/.venv"
"$REMOTE_DIR/.venv/bin/pip" install --upgrade pip uv
"$REMOTE_DIR/.venv/bin/pip" install -r "$REMOTE_DIR/backend/requirements.txt" -e "$REMOTE_DIR/publisher"
(
  cd "$REMOTE_DIR/narratoai"
  UV_PROJECT_ENVIRONMENT="$REMOTE_DIR/narratoai/.venv" "$REMOTE_DIR/.venv/bin/uv" sync --frozen --no-dev
)
python3 -m venv "$REMOTE_DIR/services/.venv"
"$REMOTE_DIR/services/.venv/bin/pip" install -r "$REMOTE_DIR/services/requirements.txt"

"$REMOTE_DIR/.venv/bin/patchright" install-deps chromium
PW="$REMOTE_DIR/ms-playwright"
mkdir -p "$PW/chromium-1208" "$PW/chromium_headless_shell-1208" "$PW/ffmpeg-1011"
CFT=https://cdn.npmmirror.com/binaries/chrome-for-testing/145.0.7632.6/linux64
if [ ! -f "$PW/chromium-1208/INSTALLATION_COMPLETE" ]; then
  curl -sfL "$CFT/chrome-linux64.zip" -o /tmp/video-hub-chrome.zip
  unzip -qo /tmp/video-hub-chrome.zip -d "$PW/chromium-1208/"
  chmod +x "$PW/chromium-1208/chrome-linux64/chrome"
  touch "$PW/chromium-1208/INSTALLATION_COMPLETE"
  rm /tmp/video-hub-chrome.zip
fi
if [ ! -f "$PW/chromium_headless_shell-1208/INSTALLATION_COMPLETE" ]; then
  curl -sfL "$CFT/chrome-headless-shell-linux64.zip" -o /tmp/video-hub-shell.zip
  unzip -qo /tmp/video-hub-shell.zip -d "$PW/chromium_headless_shell-1208/"
  chmod +x "$PW/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell"
  touch "$PW/chromium_headless_shell-1208/INSTALLATION_COMPLETE"
  rm /tmp/video-hub-shell.zip
fi
if [ ! -f "$PW/ffmpeg-1011/INSTALLATION_COMPLETE" ]; then
  curl -sfL https://cdn.npmmirror.com/binaries/playwright/builds/ffmpeg/1011/ffmpeg-linux.zip -o /tmp/video-hub-ffmpeg.zip
  unzip -qo /tmp/video-hub-ffmpeg.zip -d "$PW/ffmpeg-1011/"
  chmod +x "$PW/ffmpeg-1011/ffmpeg-linux"
  touch "$PW/ffmpeg-1011/INSTALLATION_COMPLETE"
  rm /tmp/video-hub-ffmpeg.zip
fi

chown -R narratoai:narratoai \
  "$REMOTE_DIR/data" "$REMOTE_DIR/models" "$REMOTE_DIR/ms-playwright" \
  "$REMOTE_DIR/publisher/cookies" "$REMOTE_DIR/publisher/db" \
  "$REMOTE_DIR/publisher/logs" "$REMOTE_DIR/publisher/media" \
  "$REMOTE_DIR/narratoai" "$REMOTE_DIR/services"
if [ -f "$REMOTE_DIR/.env" ]; then
  chown narratoai:narratoai "$REMOTE_DIR/.env"
  chmod 600 "$REMOTE_DIR/.env"
fi

install -m 644 "$REMOTE_DIR"/deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable video-hub-backend narratoai narrato-preview narrato-asr
systemctl restart narrato-asr narrato-preview narratoai video-hub-backend

wait_for() {
  local url="$1"
  local name="$2"
  for attempt in {1..45}; do
    if curl -sf "$url" >/dev/null; then
      echo "$name 健康检查通过"
      return 0
    fi
    if [ "$attempt" -eq 45 ]; then
      echo "$name 健康检查失败" >&2
      return 1
    fi
    sleep 2
  done
}
wait_for http://127.0.0.1:8600/health narrato-asr
wait_for http://127.0.0.1:8502/health narrato-preview
wait_for http://127.0.0.1:8501/narrato/_stcore/health narratoai
wait_for http://127.0.0.1:8000/api/health video-hub-backend

install -m 644 "$REMOTE_DIR/deploy/nginx-host.conf" /etc/nginx/sites-available/video-hub
ln -sfn /etc/nginx/sites-available/video-hub /etc/nginx/sites-enabled/video-hub
rm -f /etc/nginx/sites-enabled/narratoai
nginx -t
systemctl reload nginx
echo "统一入口已切换"
