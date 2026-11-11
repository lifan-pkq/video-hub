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
  /etc/systemd/system/narrato-engine.service \
  /etc/systemd/system/narrato-preview.service \
  /etc/systemd/system/narrato-asr.service \
  /opt/NarratoAI/config.toml; do
  if [ -f "$file" ]; then
    cp -a --parents "$file" "$BACKUP_DIR/"
  fi
done
echo "配置备份：$BACKUP_DIR"

apt-get update
apt-get install -y --no-install-recommends python3-venv python3-pip ffmpeg curl rsync unzip git

if ! id -u narratoai >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$REMOTE_DIR" --shell /usr/sbin/nologin narratoai
fi

DLR_DIR="${VIDEO_HUB_DLR_DIR:-/opt/DouyinLiveRecorder}" bash "$REMOTE_DIR/deploy/install-dlr.sh"

mkdir -p "$REMOTE_DIR"/{data,models}
# 源码不再包含 narratoai/。这里只在服务器还没有旧目录时，从 /opt/NarratoAI 拷一份媒体，
# 供 migrate_legacy.py 读取。不建虚拟环境，也不启动旧引擎。
mkdir -p "$REMOTE_DIR/narratoai"
if [ ! -f "$REMOTE_DIR/narratoai/config.toml" ] && [ -f /opt/NarratoAI/config.toml ]; then
  cp -a /opt/NarratoAI/config.toml "$REMOTE_DIR/narratoai/config.toml"
fi
if [ ! -d "$REMOTE_DIR/narratoai/storage" ] && [ -d /opt/NarratoAI/storage ]; then
  cp -a /opt/NarratoAI/storage "$REMOTE_DIR/narratoai/storage"
fi
for directory in videos scripts bgm songs srt fonts safe_areas tts_reference_audio; do
  if [ -d "/opt/NarratoAI/resource/$directory" ]; then
    mkdir -p "$REMOTE_DIR/narratoai/resource/$directory"
    cp -an "/opt/NarratoAI/resource/$directory/." "$REMOTE_DIR/narratoai/resource/$directory/"
  fi
done
if [ ! -f "$REMOTE_DIR/models/faster-whisper-small/model.bin" ] && [ -f /opt/narrato-services/models/faster-whisper-small/model.bin ]; then
  cp -a /opt/narrato-services/models/faster-whisper-small "$REMOTE_DIR/models/"
fi

python3 -m venv --clear "$REMOTE_DIR/.venv"
"$REMOTE_DIR/.venv/bin/pip" install --upgrade pip
"$REMOTE_DIR/.venv/bin/pip" install -r "$REMOTE_DIR/backend/requirements.txt"

chown -R narratoai:narratoai \
  "$REMOTE_DIR/data" "$REMOTE_DIR/models" \
  "$REMOTE_DIR/narratoai"
if [ -f "$REMOTE_DIR/.env" ]; then
  chown narratoai:narratoai "$REMOTE_DIR/.env"
  chmod 600 "$REMOTE_DIR/.env"
fi

runuser -u narratoai -- "$REMOTE_DIR/.venv/bin/python" "$REMOTE_DIR/backend/scripts/migrate_legacy.py" || true
install -m 644 "$REMOTE_DIR"/deploy/systemd/*.service /etc/systemd/system/
# 停掉不再使用的常驻进程。不删除 /opt 下的媒体、模型和已有虚拟环境。
for unit in narratoai narrato-engine narrato-preview narrato-asr video-hub-edit-narrate; do
  systemctl disable --now "$unit" 2>/dev/null || true
  rm -f "/etc/systemd/system/${unit}.service"
done
rm -rf /etc/systemd/system/video-hub-backend.service.d
if [ -s "$REMOTE_DIR/models/faster-whisper-small/model.bin" ]; then
  install -d /etc/systemd/system/video-hub-backend.service.d
  printf '[Service]\nEnvironment=VIDEO_HUB_WHISPER_MODEL=%s\n' \
    "$REMOTE_DIR/models/faster-whisper-small" \
    > /etc/systemd/system/video-hub-backend.service.d/whisper.conf
  echo "复用已有 whisper 模型：$REMOTE_DIR/models/faster-whisper-small"
fi
if [ -f "$REMOTE_DIR/.env" ]; then
  # 只去掉指向本机已停服务的地址。别的远端 ASR 地址保留。
  sed -i -E \
    -e '/^[[:space:]]*(export[[:space:]]+)?VIDEO_HUB_ASR_URL=.*(127\.0\.0\.1|localhost):8600/d' \
    -e '/^[[:space:]]*(export[[:space:]]+)?VIDEO_HUB_NARRATO_ENGINE_URL=/d' \
    -e '/^[[:space:]]*(export[[:space:]]+)?VIDEO_HUB_NARRATO_PREVIEW_URL=/d' \
    "$REMOTE_DIR/.env"
fi
systemctl daemon-reload
systemctl enable video-hub-backend
systemctl restart video-hub-backend

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
  for unit in narratoai narrato-engine narrato-preview narrato-asr video-hub-edit-narrate video-hub-backend; do
    restore_path "/etc/systemd/system/$unit.service"
  done
  systemctl daemon-reload
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
if ! wait_for http://localhost/api/health video-hub-public; then
  rollback
  exit 1
fi
echo "统一入口已切换"
