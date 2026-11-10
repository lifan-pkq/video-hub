# Video Hub 视频中枢

本地视频创作工作台：素材入库 → 抖音竖屏一键成片 → 封面与导出。发布由你自己完成，工作台不会登录或上传到抖音。

本机部署默认开启口令（`VIDEO_HUB_AUTH_MODE=enforce`）。不要把无认证 API 暴露到公网或不可信网络。

## 主工作流

1. **素材**：yt-dlp 下载、本地高清上传、手动直播分段录制、AI 图片/视频。
2. **智能剪辑**：选一条素材库原片，本机按需转写后抽出多条竖屏短视频（字幕、封面、标题/话题/简介）。
3. **导出**：单条或整包下载；也可以入库后做横屏、竖屏、方形、字幕和封面套装。

## 页面

- 视频/图片素材库：质量分、素材卡、标题与内容搜索。
- 视频下载与灵感：只走 yt-dlp；灵感库只存链接和笔记，不抓作者视频。
- 智能剪辑：默认是抖音一键成片。选一条素材库原片，转写后抽出多条竖屏短视频（字幕、封面、标题/话题/简介），在页面里预览、下载，或入库后走封面与导出。不自动发布。手动切点、废片和单条时间线留在「高级」。
- AI 素材：人物/场景/风格档案、一致性检查、morph、最多 10 个连续镜头批量生成。
- 封面与导出：封面/标题选择和整套下载。
- 创作档案：人物、场景、风格、默认偏好与发音词典。
- 设置 → 系统：存储、日志和服务状态。
- 手动录制：每 10 分钟一段，独立入库、质量评分和素材卡。

## AI 漫剧

- 创建项目时只选择集数；AI 根据每集剧情自主决定镜头数，并为每个镜头选择 4–12 秒时长。
- 图片与视频按 100 分制质检，默认 70 分合格（创建时可调整）；低于合格线会持续自动重试，直到合格或手动取消，不设置最大质量重试次数。
- 全部镜头图片合格后，整部作品的全部镜头视频同时提交生成，各自独立质检与重试。
- 项目详情支持单镜编辑提示词、参考照片和时长，也可按图片或视频阶段重试、取消单个镜头，不影响其他镜头。
- 任务中心每个漫剧项目只显示一个父任务；内部镜头及质量重试不会创建额外的全局任务，点击父任务进入对应漫剧详情。

## 本地开发

要求：Python 3.12、Node 20+、FFmpeg。

```bash
# 后端（在仓库根目录，让它能找到 engine/）
cd backend
uv venv --python 3.12
uv pip install -r requirements.txt
cd ..
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=. backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

# 前端
cd frontend
npm install
npm run dev
```

打开 http://127.0.0.1:5173/ 。前端把 `/api` 代理到 8000。

抖音一键成片在「智能剪辑」页：选原片，可选写一句想留的内容，点「生成抖音短视频」。任务页里可以预览竖屏成片，下载单条或整包（视频、封面、`copy.txt`），或入库后到「封面与导出」打包。需要自己剪时，展开「高级：手动切点和单条时间线」。

转写不另起服务。`VIDEO_HUB_ASR_URL` 留空时，后端在这次任务里加载 faster-whisper，超过 300 秒会先抽音频再分段，避免整段波形进内存。旧的共享 ASR（8600）、解说引擎（8601）、字幕预览（8502）和 `edit-narrate` 旁路都不再启动。

## 主要配置

| 变量 | 默认 | 用途 |
|---|---|---|
| `VIDEO_HUB_DATA_DIR` | `../data` | 视频、封面、数据库、档案和导出 |
| `VIDEO_HUB_AI_BASE_URL` | `https://api.xiaomimimo.com/v1` | 文本模型 |
| `VIDEO_HUB_AI_API_KEY` | 无 | 文本模型 Key |
| `VIDEO_HUB_AI_MODEL` | `mimo-v2.5` | 文本模型名 |
| `VIDEO_HUB_VISION_BASE_URL` | 文本接口 | 视觉接口 |
| `VIDEO_HUB_VISION_API_KEY` | 文本 Key | 视觉 Key |
| `VIDEO_HUB_VISION_MODEL` | 无 | 视觉模型名；未配置时视觉检查标 unknown |
| `VIDEO_HUB_ASR_URL` | 无 | 可选的远端转写地址。留空则本机 faster-whisper 按需分段转写，不启动 8600 |
| `VIDEO_HUB_WHISPER_MODEL` | `small` | faster-whisper 模型名或本地目录。服务器上已有模型时可指到该目录 |
| `VIDEO_HUB_DOUYIN_MAX_CLIPS` | `10` | 一键成片最多条数，1–10。长原片大约每 70 秒一条，短片更少 |
| `VIDEO_HUB_DOUYIN_MIN_SECONDS` | `12` | 单条最短秒数 |
| `VIDEO_HUB_DOUYIN_MAX_SECONDS` | `60` | 单条最长秒数 |
| `VIDEO_HUB_DOUYIN_WIDTH` / `VIDEO_HUB_DOUYIN_HEIGHT` | `1080` / `1920` | 竖屏输出。内存紧时可改成 `720` / `1280` |
| `VIDEO_HUB_DOUYIN_FFMPEG_THREADS` | `1` | 单条编码线程，最大 2 |
| `AGNES_MEDIA_BASE_URL` | `https://api.agnes-ai.cn/v1` | AI 图片/视频 |
| `AGNES_IMAGE_MODEL` | `agnes-image-2.5-flash` | 图片模型 |
| `AGNES_VIDEO_MODEL` | `agnes-video-2.5-flash` | 视频模型 |
| `VIDEO_HUB_PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | 外部模型可访问的参考图地址 |

AI 分析会把字幕、提示词和抽帧发送给配置的模型服务。不要把密钥写进源码；使用 `.env` 或页面 AI 模型设置。

## 测试

```bash
cd backend
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=.. .venv/bin/python -m unittest discover -s tests -v

cd ../frontend
npm run build
```

外部模型在单元测试中使用本地假服务。真实生成质量仍需观看/试听确认，不能把自动分数当作审美证明。

## 数据与迁移

数据库、档案、理解、分镜、导出均保存在 `data/`。旧 Narrato 成片只通过以下幂等脚本迁移成功文件，不迁任务状态、不删除历史媒体：

```bash
cd backend
.venv/bin/python scripts/migrate_legacy.py
```

报告：`data/migrations/latest-legacy-report.json`。自动扫描只导入 `quality_report.json` 中 `passed=true` 的成片。

## 部署

```bash
./deploy.sh
```

默认部署到 `root@39.105.112.189:/opt/video-hub`，可用 `SERVER_USER`、`SERVER_IP` 覆盖。脚本运行测试和前端构建，再同步代码、安装依赖、执行迁移、只启动 `video-hub-backend`，并停用 `narrato-engine`、`narrato-preview`、`narrato-asr`、`video-hub-edit-narrate`。Nginx 只反代 `/api/`。

同步排除 `.env`、数据库、媒体和模型。配置备份在 `/opt/video-hub-backups/<时间戳>`。这些目录里的旧媒体不会删除。

Docker Compose 只有 `backend` 和 `web`。生产机用的是 systemd，不是 Compose。

若 `/opt/video-hub/.env` 里还有 `VIDEO_HUB_ASR_URL=http://127.0.0.1:8600/...`，部署脚本会删掉这一行，否则后端还会去连已经停掉的 ASR。指向其他主机的 `VIDEO_HUB_ASR_URL` 会保留。内存紧时可以在 `.env` 加 `VIDEO_HUB_DOUYIN_WIDTH=720` 和 `VIDEO_HUB_DOUYIN_HEIGHT=1280`，不必改仓库默认值。

## 已移除的旧链路

- 常驻解说引擎（8601）、共享 ASR（8600）、字幕预览（8502），以及 `edit-narrate` 旁路（8765）。转写改到后端进程内按需执行。
- 工作台里的「AI 解说」入口。旧地址 `/narrate` 会回到智能剪辑。
- 旧 Streamlit 与 `legacy-tasks` 页面/API。
- 浏览器自动发布、扫码登录、短信验证码和平台账号页；发布替换为“封面与导出”。
- 主播监控自动开录；保留手动录制。
- 作者主页抓取；替换为只存链接和笔记的灵感库。
- 抖音/快手手写 HTML 下载器；下载只保留 yt-dlp 与本地上传。
