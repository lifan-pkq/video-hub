# 已停用

不要再安装 `video-hub-edit-narrate.service`。主站部署会停用并删除这个单元，不会删除 `/opt/video-hub/data` 里的媒体。

# 接到现有 Video Hub

目标机器上的主服务一般是：

- FastAPI `127.0.0.1:8000`，页面和 `/api` 已经在跑
- 可选 ASR `127.0.0.1:8600`
- 旧解说引擎 `127.0.0.1:8601`（这一套不再依赖它）

本目录只替换「智能剪辑」和「AI 解说」。其它页面不要动。

## 推荐：旁路进程，不改主站路由

主站继续提供素材库和其它页面。本服务自己听一个端口，读同一批视频。

```bash
cd /opt/video-hub
# 把本仓库同步到 /opt/video-hub/edit-narrate ，不要覆盖主站的 app/db.py、app/main.py
cd /opt/video-hub/edit-narrate/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 有网且要人声时再装：.venv/bin/pip install edge-tts
```

`/etc/video-hub/edit-narrate.env`：

```bash
VIDEO_HUB_DATA_DIR=/opt/video-hub/data
VIDEO_HUB_VIDEOS_DIR=/opt/video-hub/data/videos
VIDEO_HUB_EXTERNAL_DB=/opt/video-hub/data/app.db
VIDEO_HUB_SEED_DEMO=0
VIDEO_HUB_JOBS_INLINE=0
VIDEO_HUB_AI_BASE_URL=https://你的兼容网关/v1
VIDEO_HUB_AI_API_KEY=从环境注入
VIDEO_HUB_AI_MODEL=你的模型名
VIDEO_HUB_VISION_MODEL=
VIDEO_HUB_ASR_URL=http://127.0.0.1:8600/v1/audio/transcriptions
VIDEO_HUB_TTS_BASE_URL=
VIDEO_HUB_TTS_API_KEY=
```

`VIDEO_HUB_EXTERNAL_DB` 指到主库的 sqlite。表名可以是 `videos` 或 `video`，至少要有 `id`，以及 `filename` 或 `path`。时长、宽高有则用。入库时按这些列插入；插不进去时成片仍留在 videos 目录，任务里会写明原因。

确认库文件：

```bash
find /opt/video-hub/data -name '*.db' -o -name '*.sqlite'
```

systemd `video-hub-edit-narrate.service`：

```ini
[Service]
WorkingDirectory=/opt/video-hub/edit-narrate
EnvironmentFile=/etc/video-hub/edit-narrate.env
ExecStart=/opt/video-hub/edit-narrate/backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765
Restart=on-failure
```

前端：

```bash
cd /opt/video-hub/edit-narrate/frontend
npm ci
npm run build
```

构建后的 `frontend/dist` 由同一个 uvicorn 挂在 `/`（API 路由优先）。浏览器开 `http://127.0.0.1:8765/edit` 和 `/narrate`。

Nginx 用**另一个端口或另一个 server**，不要抢主站的 `/assets`：

```nginx
server {
    listen 127.0.0.1:8096;
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_read_timeout 3600s;
    }
}
```

主站页面保持原样。需要入口时，在主站加两个外链到 `http://127.0.0.1:8096/edit` 和 `/narrate`，不要把主站的 SPA fallback 指过来。

## 文件怎么放

| 本仓库 | 旁路部署 | 不要覆盖主站 |
|---|---|---|
| `engine/` | `/opt/video-hub/edit-narrate/engine` | 旧 `narratoai/` 留着也没关系，这条链路不调用它 |
| `backend/app` | 旁路进程自己的 `app` | 主站 `app/main.py`、`app/db.py`、`app/config.py` |
| `frontend/` | 旁路静态页 | 主站 `frontend/src/pages` 其它页面 |

如果一定要把路由塞进主进程，只复制：

- `backend/app/routers/edit.py`
- `backend/app/routers/narrate.py`
- `backend/app/routers/jobs.py`
- `backend/app/services/edit_flow.py`
- `backend/app/services/narrate_flow.py`
- `backend/app/services/videos.py`
- `backend/app/jobs.py`
- 整个 `engine/`

在主站 `main.py` 里 `include_router` 这三个路由。`videos.py` 会调用主站已有的 `app.db.get_video` / `add_video`，并用主站 `VIDEOS_DIR`。不要把本仓库的 `app/db.py`、`app/config.py`、`app/main.py` 拷进去。也不要挂本仓库的 `/api/videos`，主站已经有素材库接口。

前端页面在 `frontend/src/pages/EditPage.tsx` 和 `NarratePage.tsx`。主站若是另一套组件库，优先用旁路端口，而不是把这两页硬嵌进旧的多面板。

## 环境变量

| 变量 | 作用 |
|---|---|
| `VIDEO_HUB_DATA_DIR` | 任务、解说目录、默认库 |
| `VIDEO_HUB_VIDEOS_DIR` | 成片和素材文件 |
| `VIDEO_HUB_DB` | 旁路自有 sqlite |
| `VIDEO_HUB_EXTERNAL_DB` | 只读打开主库；导出时再尝试写入 |
| `VIDEO_HUB_AI_BASE_URL` / `VIDEO_HUB_AI_API_KEY` / `VIDEO_HUB_AI_MODEL` | OpenAI 兼容 `chat/completions` |
| `VIDEO_HUB_VISION_*` | 抽帧说明。空着时帧还在，说明处写明未配置 |
| `VIDEO_HUB_ASR_URL` | 共享转写的完整 URL，字段名 `file` |
| `VIDEO_HUB_ASR_SIDECAR` | `1` 才读 `*.transcript.json`。演示片 `source_platform=demo` 自带旁路稿 |
| `VIDEO_HUB_TTS_BASE_URL` / `VIDEO_HUB_TTS_API_KEY` / `VIDEO_HUB_TTS_MODEL` | 兼容 `audio/speech`。空则尝试 edge-tts、espeak |
| `VIDEO_HUB_SEED_DEMO` | 生产设 `0` |
| `VIDEO_HUB_JOBS_INLINE` | 生产保持 `0` |

没有配文本模型时：剪辑仍给出信号切点；解说「用模型写」返回 409，并提示改用逐句骨架。没有 ASR 时：剪辑的静音/黑场/镜头仍在，任务状态为 `partial`；解说理解会留下画面，并要求手写转写。没有 TTS 时渲染直接失败，并列出 edge-tts、兼容接口、espeak 三条路。`tone` 只在审阅里手选，成片上会标明这是测试音。

## 接口

剪辑：

- `POST /api/edit/analyze` `{video_ids}` → 任务。结果里有 `signals`、`suggestions`、转写
- `POST /api/edit/plan` `{video_id, suggestions, use_llm}` → 信号方案或模型裁决
- `POST /api/edit/export` `{title, clips:[{video_id,start,end}]}` → 任务，ffmpeg 拼接
- `POST /api/edit/concat` 与 export 相同，兼容旧调用
- `GET /api/jobs/{id}`，`POST /api/jobs/{id}/cancel`，`GET /api/jobs/{id}/file`

解说：

- `POST /api/narrate/tasks` `{video_id, max_frames}` 理解
- `PUT /api/narrate/tasks/{id}/transcript`
- `POST /api/narrate/tasks/{id}/script` `{mode: llm|skeleton, instruction, tone, target_duration}`
- `PUT /api/narrate/tasks/{id}/script` 审阅后的 beats 和 voice
- `POST /api/narrate/tasks/{id}/render`
- `POST /api/narrate/tasks/{id}/import`
- `GET /api/narrate/tasks/{id}/output`
- `POST /api/narrate/tts-preview`

任务状态是 `pending` / `running` / `success` / `partial` / `failed` / `cancelled`。`partial` 和 `failed` 都带 `error.message` 与 `error.action`。

## 引擎

渲染在后端进程里调用 `engine.render`。不需要旧的 NarratoAI worker。

只要健康检查时：

```bash
backend/.venv/bin/python -m engine.server --host 127.0.0.1 --port 8601
```

这只回答 `GET /health`，不接生成任务。原来的 8601 若仍被别的页面使用，就不要占用这个端口。

## 验收

```bash
cd /opt/video-hub/edit-narrate
backend/.venv/bin/python -m pytest
curl -s http://127.0.0.1:8765/api/health
```

浏览器：

1. `/edit`：演示素材或库里的一条视频加入时间线 → 分析切点 → 能看见静音/黑场/镜头 → 接受一条删除 → 导出，进度走完并能播放。
2. `/narrate`：理解后能改转写和看到帧 → 无模型时「用模型写」出现可执行的错误 → 「逐句骨架」得到可编辑段 → 审阅后渲染。未装 TTS 时应失败并说明怎么装，而不是下一个空文件。
