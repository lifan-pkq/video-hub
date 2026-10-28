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
  /etc/nginx/sites-available/video-hub \
  /etc/nginx/sites-enabled/video-hub \
  /etc/systemd/system/video-hub-backend.service \
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
for directory in videos scripts bgm songs srt fonts safe_areas tts_reference_audio; do
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
if [ ! -s "$REMOTE_DIR/models/faster-whisper-small/model.bin" ]; then
  echo "whisper-small 模型缺失，无法提供 ASR 服务" >&2
  exit 1
fi

python3 -m venv --clear "$REMOTE_DIR/.venv"
"$REMOTE_DIR/.venv/bin/pip" install --upgrade pip uv
"$REMOTE_DIR/.venv/bin/pip" install -r "$REMOTE_DIR/backend/requirements.txt" -e "$REMOTE_DIR/publisher"
(
  cd "$REMOTE_DIR/narratoai"
  "$REMOTE_DIR/.venv/bin/uv" export --frozen --format requirements-txt --no-dev --no-hashes -o /tmp/narrato-lock-requirements.txt
)
python3 -m venv --clear "$REMOTE_DIR/narratoai/.venv"
"$REMOTE_DIR/narratoai/.venv/bin/pip" install -r /tmp/narrato-lock-requirements.txt
python3 -m venv --clear "$REMOTE_DIR/services/.venv"
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

chown narratoai:narratoai "$REMOTE_DIR/publisher"
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
ffmpeg -y -v error -f lavfi -i anullsrc=r=16000:cl=mono -t 0.5 /tmp/video-hub-asr-smoke.wav
curl -sf --max-time 300 \
  -F 'file=@/tmp/video-hub-asr-smoke.wav;type=audio/wav' \
  -F 'response_format=json' \
  http://127.0.0.1:8600/v1/audio/transcriptions >/dev/null
rm -f /tmp/video-hub-asr-smoke.wav
wait_for http://127.0.0.1:8600/health narrato-asr-ready
wait_for http://127.0.0.1:8502/health narrato-preview
wait_for http://127.0.0.1:8501/narrato/_stcore/health narratoai
wait_for http://127.0.0.1:8000/api/health video-hub-backend

TEST_NGINX_CONF=/tmp/video-hub-nginx-check.conf
printf 'events {}\nhttp { include /etc/nginx/mime.types; include %s; }\n' "$REMOTE_DIR/deploy/nginx-host.conf" > "$TEST_NGINX_CONF"
nginx -t -c "$TEST_NGINX_CONF"
rm -f "$TEST_NGINX_CONF"

restore_path() {
  local target="$1"
  mkdir -p "$(dirname "$target")"
  rm -f "$target"
  if [ -e "$BACKUP_DIR$target" ] || [ -L "$BACKUP_DIR$target" ]; then
    cp -a "$BACKUP_DIR$target" "$target"
  fi
}
rollback() {
  echo "统一切换失败，恢复旧配置" >&2
  restore_path /etc/nginx/sites-enabled/narratoai
  restore_path /etc/nginx/sites-enabled/video-hub
  restore_path /etc/nginx/sites-available/video-hub
  for unit in narratoai narrato-preview narrato-asr video-hub-backend; do
    restore_path "/etc/systemd/system/$unit.service"
  done
  systemctl daemon-reload
  systemctl restart narratoai narrato-preview narrato-asr
  if [ ! -f /etc/systemd/system/video-hub-backend.service ]; then
    systemctl stop video-hub-backend 2>/dev/null || true
  else
    systemctl restart video-hub-backend
  fi
  nginx -t || true
  systemctl reload nginx || true
}

install -m 644 "$REMOTE_DIR/deploy/nginx-host.conf" /etc/nginx/sites-available/video-hub
ln -sfn /etc/nginx/sites-available/video-hub /etc/nginx/sites-enabled/video-hub
rm -f /etc/nginx/sites-enabled/narratoai
if ! nginx -t; then
  rollback
  exit 1
fi
systemctl reload nginx
if ! wait_for http://localhost/api/health video-hub-public || \
   ! wait_for http://localhost/narrato/_stcore/health narrato-public || \
   ! wait_for http://localhost/preview/health preview-public; then
  rollback
  exit 1
fi
echo "统一入口已切换"
