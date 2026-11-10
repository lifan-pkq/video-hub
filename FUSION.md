# Video Hub 融合说明

这次把信号优先的智能剪辑和四步 AI 解说并进主应用。对外仍是一套 FastAPI（:8000）加一套 React SPA，由 nginx :80 反代。不要再为剪辑/解说单独开 8096。

## 改了什么

- `backend/app/routers/edit.py` 换成分析切点、模型精排、导出。`POST /api/edit/concat` 仍是导出的别名。
- `backend/app/routers/narrate.py` 换成四步：理解 → 文稿 → 审阅 → 渲染。不再请求 NarratoAI :8601。
- 长任务写在 `{DATA_DIR}/jobs/*.json`，解说产物在 `{DATA_DIR}/narrate/{id}/`。`GET /api/tasks` 和 `/api/ws` 快照会把这些任务并进任务中心。失败体是 `{code, message, action}`。
- `engine/` 放在仓库根。`app.main` 会把仓库根加入 `sys.path`，因此 `uvicorn app.main:app --app-dir backend` 也能 `import engine`。
- 前端 `/edit`、`/edit/new`、`/edit/:taskId` 都是同一剪辑台。素材库的「智能剪辑」仍用 `state.videoIds`。`/narrate?video=` 和 `/narrate?task=` 仍可用。
- `/workbench` 不再复制概览页，改成最近素材并跳进剪辑、解说、导出。
- 剪映草稿按钮保留为「暂未接入」，避免点进已删除的导出接口。
- 旧的 `backend/app/services/narration.py` 还在，只给历史导入循环和档案快照用。新页面不调用它。

## 环境变量

沿用现有 Video Hub 配置，鉴权保持关闭：

```bash
VIDEO_HUB_AUTH_MODE=off
VIDEO_HUB_DATA_DIR=/opt/video-hub/data
VIDEO_HUB_ASR_URL=http://127.0.0.1:8600/v1/audio/transcriptions
VIDEO_HUB_AI_BASE_URL=
VIDEO_HUB_AI_API_KEY=
VIDEO_HUB_AI_MODEL=
VIDEO_HUB_VISION_BASE_URL=
VIDEO_HUB_VISION_API_KEY=
VIDEO_HUB_VISION_MODEL=
```

- 剪辑的静音、黑场、镜头不需要模型。
- 「模型精排」和「用模型写」需要 `VIDEO_HUB_AI_*` 三者都有值，否则接口返回 409，正文里有 `message` 和 `action`。
- 画面说明需要 `VIDEO_HUB_VISION_MODEL`。不配时抽帧成功，每帧带 `caption_error`。
- 人声优先 edge-tts。可选：`VIDEO_HUB_TTS_BASE_URL`、`VIDEO_HUB_TTS_API_KEY`、`VIDEO_HUB_TTS_MODEL`、`VIDEO_HUB_TTS_VOICE`。
- 不要设置 `VIDEO_HUB_JOBS_INLINE=1`（那是测试开关）。

## 要补的 pip 包

在现有 backend 虚拟环境里：

```bash
pip install -r /opt/video-hub/backend/requirements.txt
```

相对旧环境新增：

- `edge-tts`（解说人声；已写进 requirements.txt）
- 已有的 `httpx` 继续用于共享 ASR

不需要为新解说安装 NarratoAI 依赖。ffmpeg / ffprobe 仍必须在 PATH 里。

## 换到服务器

在仓库根目录打包（不含 node_modules、.venv、data）：

```bash
# 见 /opt/cursor/artifacts/video-hub-fused.tgz
cd /opt/video-hub
# 先备份
sudo systemctl stop video-hub-backend
# 同步代码，保留 data、.env、narratoai 目录
rsync -a --delete \
  --exclude data --exclude .env --exclude narratoai \
  --exclude node_modules --exclude .venv --exclude frontend/node_modules \
  /path/to/this/tree/ /opt/video-hub/
cd /opt/video-hub/frontend && npm ci && npm run build
/opt/video-hub/backend/.venv/bin/pip install -r /opt/video-hub/backend/requirements.txt
sudo systemctl restart video-hub-backend
```

systemd 的 `WorkingDirectory` 若是 `backend/`，确认单元里有：

```ini
Environment=PYTHONPATH=/opt/video-hub
Environment=VIDEO_HUB_AUTH_MODE=off
```

`app.main` 也会自行把仓库根放进 `sys.path`，两条都写上更稳。

前端构建产物放到 nginx 的站点根（与现在一样）。后端只监听 127.0.0.1:8000。

## nginx

只保留主站 :80：

- `/` → 前端 `index.html`（SPA）
- `/api/` → `http://127.0.0.1:8000`
- WebSocket `/api/ws` 继续反代

8096 上的剪辑/解说 sidecar 可以停掉并删掉对应 `location`。NarratoAI :8601 若还被别的旧页面使用可以留着，新的 `/narrate` 不会访问它。

## 本地验证

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=. pytest backend/tests/test_signal_pipeline.py engine/tests -q
cd frontend && npm run build
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=. uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```
