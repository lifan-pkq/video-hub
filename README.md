# Video Hub 视频中枢

本地视频创作工作台：高清素材 → 融合理解 → 创作档案 → 剧本两轮 → 分镜与多版本审片 → 配音/字幕/画面精修 → 质量门 → 成品套装导出。

本项目按仅本机使用设计，无需登录，不要把无认证 API 暴露到公网或不可信网络。

## 主工作流

1. **素材**：yt-dlp 下载、本地高清上传、手动直播分段录制、AI 图片/视频。
2. **融合理解**：口播、画面、声音在同一条绝对时间轴；保存摘要、人物、场景、OCR、主体框、静音、节拍候选、结构与情绪。说话人分离依赖 ASR 返回值，不能识别时明确标 `speaker_unknown`。
3. **创作档案**：人物固定特征/服装/年龄规则/参考图、场景摆设与光线、系列字幕/色彩/音乐/旁白、专属发音词典。任务保存档案快照，后续修改不影响历史。
4. **剧本两轮**：初稿 → 独立批判 → 不合格段定向重写；检查事实、空话、重复、钩子与时长，仍需确认的红色段进入审片。
5. **分镜与多版本**：关键帧、选段理由、预计时长；A/B/C 三版对比，支持跨版本逐段拼成新版。关键 morph 必须首尾帧并遵守同一人物、同一场景约束。
6. **渲染**：逐段音色/语速/情绪、10 秒试听、动态 ASS 字幕、背景音乐 ducking、降噪去齿音、响度统一、色彩/锐化与主体跟拍。
7. **质量门**：对齐、字幕、声音、结构、画面检查。严重问题进入 `needs_review`，不会自动入库；人工确认后才能复核入库。
8. **导出**：横屏、竖屏、方形、带字幕、无字幕、SRT、三张封面、标题简介，一次打成 ZIP。

## 页面

- 视频/图片素材库：质量分、素材卡、标题与内容搜索。
- 视频下载与灵感：只走 yt-dlp；灵感库只存链接和笔记，不抓作者视频。
- 智能剪辑：故事弧选段、句末/镜头/节拍吸附、理解分轨、撤销/重做和命名快照。
- AI 解说：融合理解、剧本批判、分镜、三版与跨版拼段、结构审片、TTS 试听。
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
# 后端
cd backend
uv venv --python 3.12
uv pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# AI 解说引擎
cd narratoai
uv venv --python 3.12
uv pip install -r requirements.txt
cp -n config.example.toml config.toml
.venv/bin/python -m engine.server --host 127.0.0.1 --port 8601

# 前端
cd frontend
npm install
npm run dev
```

打开 http://127.0.0.1:5173/ 。前端把 `/api` 代理到 8000。

共享 ASR（8600）和字幕预览（8502）是可选服务，不启动不会影响主工作台。自动转写未配置共享 ASR 时使用后端本地 faster-whisper。旧 Streamlit（8501）已下线，不要作为生产入口。

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
| `VIDEO_HUB_ASR_URL` | 无 | 可选共享 ASR transcriptions URL |
| `VIDEO_HUB_NARRATO_ENGINE_URL` | `http://127.0.0.1:8601` | 解说引擎 |
| `AGNES_MEDIA_BASE_URL` | `https://api.agnes-ai.cn/v1` | AI 图片/视频 |
| `AGNES_IMAGE_MODEL` | `agnes-image-2.5-flash` | 图片模型 |
| `AGNES_VIDEO_MODEL` | `agnes-video-2.5-flash` | 视频模型 |
| `VIDEO_HUB_PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | 外部模型可访问的参考图地址 |

AI 分析会把字幕、提示词和抽帧发送给配置的模型服务。不要把密钥写进源码；使用 `.env` 或页面 AI 模型设置。

## 测试

```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v

cd ../narratoai
.venv/bin/python -m unittest discover -s engine/tests -v

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
SSHPASS='服务器密码' ./deploy.sh
```

默认部署到 `root@39.105.112.189:/opt/video-hub`，可用 `SERVER_USER`、`SERVER_IP` 覆盖。脚本运行测试和前端构建，再同步代码、安装依赖、执行迁移、启动 `video-hub-backend`、`narrato-engine`、`narrato-preview`、`narrato-asr` 并检查 Nginx。

同步排除 `.env`、数据库、媒体、模型和 Narrato 真实配置。配置备份在 `/opt/video-hub-backups/<时间戳>`。旧 8501 服务会停止并禁用，但旧媒体不会删除。

Docker Compose 包含 `backend`、`web`、`narrato-engine` 三个服务，挂载 `./data` 和 `./narratoai/storage`，不包含旧 Streamlit。

## 已移除的旧链路

- 旧 Streamlit 与 `legacy-tasks` 页面/API。
- 浏览器自动发布、扫码登录、短信验证码和平台账号页；发布替换为“封面与导出”。
- 主播监控自动开录；保留手动录制。
- 作者主页抓取；替换为只存链接和笔记的灵感库。
- 抖音/快手手写 HTML 下载器；下载只保留 yt-dlp 与本地上传。
