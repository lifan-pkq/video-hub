#!/bin/bash
# 安装/更新抖音直播解析依赖 DouyinLiveRecorder（Video Hub 直播录制使用）。
set -euo pipefail

DLR_DIR="${DLR_DIR:-/opt/DouyinLiveRecorder}"
PYTHON="${PYTHON:-python3}"
REPO="https://github.com/ihmily/DouyinLiveRecorder.git"

if [ ! -d "$DLR_DIR/.git" ]; then
  rm -rf "$DLR_DIR"
  git clone --depth 1 "$REPO" "$DLR_DIR"
else
  # 失败不阻塞已有版本继续使用
  git -C "$DLR_DIR" pull --ff-only >/dev/null 2>&1 || true
fi

if [ ! -x "$DLR_DIR/.venv/bin/python" ]; then
  "$PYTHON" -m venv "$DLR_DIR/.venv"
fi
"$DLR_DIR/.venv/bin/pip" install -q --upgrade pip
"$DLR_DIR/.venv/bin/pip" install -q -r "$DLR_DIR/requirements.txt"

if id -u narratoai >/dev/null 2>&1; then
  chown -R narratoai:narratoai "$DLR_DIR"
fi

echo "DouyinLiveRecorder ready: $DLR_DIR"
