# Video Hub 全站重构说明

这次把导航里每个模块收成同一套壳：页头、面板、按钮、失败提示、空状态和任务中心。界面全部中文。鉴权保持关闭（`VIDEO_HUB_AUTH_MODE=off`）。没有新增 SaaS 依赖，也没有发明还没接上的云接口。

智能剪辑（信号切点）和四步 AI 解说保持原 API，只让它们继续走同一套页面壳。旧 NarratoAI :8601 不是新流程的依赖。

## 各模块

| 模块 | 状态 | 这次做了什么 |
|---|---|---|
| 工作台概览 `/` | 重写 | 只保留统计、快捷入口、最近任务和本机能力（ffmpeg / 文本模型 / 画面模型 / ASR / TTS）。不再铺最近素材。 |
| 创作工作台 `/workbench` | 重写角色 | 最近素材，一键去剪辑、解说、导出、下载。已放进侧栏「概览」。 |
| 视频素材库 `/videos` | 轻改 | 保留选择、筛选、播放、截取、重命名、删除，以及去剪辑 / 解说 / 导出。空状态改成去导入或下载。 |
| 导入 `/library/import` | 重写 | 不再是下载页的别名。两步：本地上传，或每行一条链接提交下载。完成后去剪辑、解说或任务中心。 |
| 图片素材 `/images` | 重写交互 | 与视频库同一套空态 / 错误重试。预览里可以下载、删除，或「用作参考」跳到 AI 素材并带上这张图。 |
| 视频下载 `/download` | 重写提示 | 解析和批量下载仍走 yt-dlp。空白链接、没有 URL、Cookie / 新鲜度失败会给出中文原因和下一步。进度在本页，也能去任务中心。灵感笔记还在，默认收成列表。 |
| 直播录制 `/record` | 重写提示 | 开始 / 停止 / 列表 / 边录边看还在。画质和帧率不合法时直接说明可选值。每条记录写明是否已入库、素材卡是否还在生成。 |
| 智能剪辑 `/edit` | 保持 | 信号分析、模型精排、导出 API 未改。 |
| AI 解说 `/narrate` | 保持 | 仍是理解 → 文稿 → 审阅 → 成片，人声优先 edge-tts。不请求 :8601。 |
| AI 素材 `/ai-media` | 重写失败态 | 仍用现有 `/api/ai/*` 和人物 / 场景 / 风格档案。没有 Key 或 Key 池读失败时，页面说明原因并禁用生成，不再只显示「读取失败」。 |
| 封面与导出 `/export` | 重写空态 | 没有视频时不再给一个空下拉。选中视频后生成套装，ZIP 仍走 `/api/exports/{id}/download`。素材库的「导出」会带上 `videoId`。 |
| 任务中心 `/tasks` | 重写跳转 | 内存任务和磁盘任务仍合并在 `/api/tasks`。剪辑打开 `/edit/:id`，解说打开 `/narrate?task=`，下载打开 `/download?task=`，AI 视频打开 `/ai-media?task=`。取消仍是 `POST /api/tasks/{id}/cancel`。失败显示 `message` 和 `action`。 |
| 创作档案 `/profiles` | 轻改 | 人物、场景、风格、默认偏好（含发音词典）仍用现有 CRUD。名称为空、词典行没有等号、参考图超过 5 张时会拦住。声音特征写在人物档案里，没有另起一套音色服务。 |
| 系统 `/settings/system` | 轻改 | 存储、清理、定时设置还在。补上内存 / 磁盘 / 创作能力，以及服务是否在线。旧解说引擎标明可选。清理和「定时清空视频」都要确认。结果里不展示密钥类字段。 |
| 404 | 轻改 | 回到工作台概览。 |

`/narrato` 仍跳到 `/narrate`，`/publish` 仍跳到 `/export`，`/maintenance` 仍跳到 `/settings/system`。

## 后端只动了这些

- 下载解析和批量提交的失败体是 `{detail:{code,message,action}}`。Cookie / 新鲜度、空链接、超时、入库失败都会写进任务的 `error`。
- 录制的空链接、非法画质、非法帧率同样返回 `{code,message,action}`。
- AI 图片 / 视频在没有 Key 时返回 409，`code` 为 `ai_keys_missing`，不会先创建一个注定失败的任务。
- `/api/health` 仍返回 `ffmpeg`、`llm`、`vision`、`asr`、`tts`。
- 没有重新打开鉴权墙，没有删 `data/` 约定，没有改 yt-dlp / DouyinLiveRecorder 本体。

前端 `api.ts` 删掉了已经没有页面调用的旧 Narrato 多面板类型和扫码登录常量。下载、剪辑、解说、档案、导出仍各走原来的路径。

## 环境变量

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

图片和视频生成的 Key 从这些名字里读，有一个即可：`VIDEO_HUB_AI_API_KEYS`、`VIDEO_HUB_VISION_API_KEYS`、`AGNES_API_KEYS`、`VIDEO_HUB_AI_API_KEY`、`AGNES_API_KEY`。不要写进源码，也不要填到页面上。

解说人声优先 edge-tts。可选：`VIDEO_HUB_TTS_BASE_URL`、`VIDEO_HUB_TTS_API_KEY`、`VIDEO_HUB_TTS_MODEL`、`VIDEO_HUB_TTS_VOICE`。

不要设置 `VIDEO_HUB_JOBS_INLINE=1`（那是测试开关）。

## 部署到 /opt/video-hub

在仓库根打包（不含 `node_modules`、`.venv`、`data`）。先停后端，同步代码时保留数据、环境和旧引擎目录：

```bash
sudo systemctl stop video-hub-backend
sudo rsync -a --delete \
  --exclude data \
  --exclude .env \
  --exclude narratoai \
  --exclude node_modules \
  --exclude .venv \
  --exclude frontend/node_modules \
  --exclude frontend/dist \
  /path/to/this/tree/ /opt/video-hub/
cd /opt/video-hub/frontend && npm ci && npm run build
/opt/video-hub/backend/.venv/bin/pip install -r /opt/video-hub/backend/requirements.txt
sudo systemctl restart video-hub-backend
```

systemd 若 `WorkingDirectory=/opt/video-hub/backend`：

```ini
Environment=PYTHONPATH=/opt/video-hub
Environment=VIDEO_HUB_AUTH_MODE=off
```

`app.main` 也会把仓库根放进 `sys.path`。两条都写上更稳。

前端构建产物放到现有 nginx 站点根。后端只听 `127.0.0.1:8000`。nginx 继续：

- `/` → SPA `index.html`
- `/api/` → `http://127.0.0.1:8000`
- WebSocket `/api/ws` 继续反代

这次不改 TLS、域名和 nginx 结构。8096 上的剪辑 / 解说 sidecar 可以停掉。:8601 可以留着给旧数据，新页面不会访问它。

## 本地验证

```bash
cd backend && VIDEO_HUB_AUTH_MODE=off PYTHONPATH=.. python -m pytest tests/test_shell_smoke.py tests/test_signal_pipeline.py tests/test_narrate_router.py ../engine/tests -q && cd ..
cd frontend && npm run build
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=. uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

冒烟覆盖：`/api/health` 字段、空白链接解析 400、没有 URL 的文本 400、不存在的视频创建导出 400。
