# Video Hub 视频中枢

一体化视频工作台：视频下载 / AI 解说生成 → 本地视频库 → 剪辑 → AI 素材生成，支持直播录制自动入库。

## 功能

- **仪表盘**（Bento 拼贴）：视频/图片/录制/任务统计、进行中任务、最近视频横滚，以及**服务器内存/磁盘/负载/各服务进程内存与健康状态**
- **任务中心**：点击任务跳到对应工作页面，并在右下角持续显示进度和最新日志；日志可用右侧箭头展开。
- **系统维护**：存储占用概览；一键清理日志/临时文件；一键清空视频库（含 AI 解说成片和 AI 生成视频，删除 videos 行、封面、导入台账并解除关联）；可设置定时清理（频率、日志保留天数、是否同时清空视频）
- **视频库**（海报墙）：3:4 竖版封面卡流，在线播放（支持拖动进度）、搜索、来源筛选、排序、重命名、下载到本地、**视频无损截取**（ffmpeg）、多选批量删除、本地视频上传导入
- **视频剪辑**（单轨时间线）：播放头截断、删除左/右侧、复制、精确入出点、拖动排序与拖边裁剪、撤销/重做、时间线缩放、浏览器草稿恢复；FFmpeg 服务端拼接渲染并自动入库
- **AI 智能剪辑**：共享或本地 faster-whisper 转写 + FFmpeg 真实静音检测 + LLM 开头/结尾/广告/重复表达分析；长字幕分块，建议可逐条勾选、编辑、预览和应用，保留手动时间线且支持撤销
- **AI 解说**：把 NarratoAI 变成内置生成引擎，前端是原生工作台——选素材、选内容类型（知识/纪录/生活/产品/教程/访谈/影视/通用）、写主题或粘贴文稿，生成解说、配音、字幕并合成；任务进度、取消、成片预览与自动入库都在 Video Hub 内完成，不再依赖 iframe 或手工刷新
- **视频下载**：粘贴抖音分享口令/链接，自动提取、解析并无水印下载入库（yt-dlp + 抖音 cookie，H.265 自动转码 H.264 保证浏览器可播）；**支持多行批量队列下载**（串行执行防风控）
- **直播录制**：粘贴直播间链接（抖音/B站/直链 m3u8/flv，其他平台走 yt-dlp），ffmpeg 录制，可手动停止，完成/停止后自动入库视频库；计时从 ffmpeg 真正写出首个媒体字节开始，不再把直播流解析/握手时间算进去
- **界面**：分组侧栏工作台 + 移动端底部 Dock，**Cmd/Ctrl+K 命令面板**，深色/浅色双主题，键盘操作、减少动态效果、加载/空/错误/重试状态，404 页
- **本地模式**：无需登录或访问令牌，打开即用；保留媒体格式校验与任务稳定性保护

## 架构

```
├── backend/
│   ├── app/routers/       API 入口与请求校验
│   ├── app/services/      下载、录制、媒体与任务生命周期
│   └── tests/            标准库 unittest 测试
├── frontend/src/
│   ├── api.ts            统一 HTTP 客户端与 API 类型/方法
│   ├── context/          全局任务状态与轮询
│   ├── features/editor/  片段模型、历史、预览、时间线与 AI 面板
│   ├── components/       跨页面业务组件
│   ├── pages/            路由页面（按需懒加载）
│   └── ui/               基础组件与统一请求状态
├── backend/app/services/narration.py   AI 解说领域服务：引擎客户端 + 任务台账 + 入库
├── narratoai/            NarratoAI 0.8.7（MIT）脚本、配音、字幕与视频合成
│   └── engine/           无头解说引擎：内容 Profile、脚本生成、HTTP API、隔离 worker
├── services/             独立字幕预览与共享 faster-whisper ASR
├── deploy/               Nginx、systemd 与服务器安装脚本
└── data/                 视频、封面、录制与 SQLite（运行时生成）
```

统一入口由 Nginx 提供：`/api/` 到 Video Hub 后端，`/preview/` 到字幕预览，
其他路径到 React 前端。`/narrato/` 已重定向到 `/narrate`；旧版服务仍保留，但不再作为公开主入口。

AI 解说的运行时链路：

```
React 原生工作台
  → POST /api/narrate/tasks（Video Hub 后端，SQLite 记账）
  → POST :8601/tasks（narrato-engine，独立进程）
  → worker 子进程：内容 Profile → 逐帧/文稿 → 解说脚本 → TTS → 裁剪 → 字幕 → 合成
  → combined.mp4 稳定后由后端统一入库（narrato://task/<id> 幂等去重）
```

引擎只监听本机 8601，不经过 Nginx；成片入库走 Video Hub 自己的媒体管线，
因此封面上传、视频库播放、剪辑等后续模块与普通素材完全一致。

## 剪辑快捷键与 AI 数据边界

- `S`：在播放头截断；`Delete`：删除选中片段。
- `Space`：播放/暂停；`←/→`：移动 1 秒；`Shift+←/→`：移动 5 秒。
- `Ctrl/Cmd+Z`：撤销；`Ctrl/Cmd+Shift+Z` 或 `Ctrl/Cmd+Y`：重做。
- 时间线和导出标题自动保存到当前浏览器，不上传草稿。
- 静音检测与语音转写在本地执行；只有字幕、重复候选和自定义提示会发送给配置的 LLM 服务。

## 本地开发

```bash
# 后端（Python 3.12）
cd backend && uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn app.main:app --port 8000

# AI 解说引擎（首次；与 NarratoAI 共用虚拟环境）
cd narratoai && uv venv --python 3.12 && uv pip install -r requirements.txt
cp -n config.example.toml config.toml
.venv/bin/python -m engine.server --host 127.0.0.1 --port 8601

# 前端（Node 20+）
cd frontend && npm install && npm run dev   # http://127.0.0.1:5173
```

本项目按仅本机使用设计，无需登录或访问令牌，启动后打开页面即可使用。
不要将无认证的 API 直接暴露到公网或其他不可信网络。

环境变量（默认值面向本地开发，Docker 内已覆盖）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `VIDEO_HUB_DATA_DIR` | `../data` | 数据目录 |
| `VIDEO_HUB_DLR_DIR` | `../DouyinLiveRecorder`（服务器 `/opt/DouyinLiveRecorder`） | 抖音直播解析项目目录 |
| `VIDEO_HUB_DLR_PYTHON` | `DouyinLiveRecorder/.venv/bin/python` | 抖音直播解析项目的 Python |
| `VIDEO_HUB_AI_BASE_URL` | `https://api.xiaomimimo.com/v1` | AI 剪辑的 LLM 接口（OpenAI 兼容） |
| `VIDEO_HUB_AI_API_KEY` | 无 | AI 单 Key 兜底 |
| `VIDEO_HUB_AI_API_KEYS` | 无 | 多 Key 池，逗号分隔；优先于单 Key |
| `VIDEO_HUB_VISION_API_KEYS` | 回退到文本 Key 池 | 视觉多 Key 池 |
| `AGNES_API_KEYS` | 无 | 图像/视频/文本共用 Key 池 |
| `AGNES_MEDIA_BASE_URL` | `https://api.agnes-ai.cn/v1` | Agnes 图像/视频接口 |
| `AGNES_IMAGE_MODEL` | `agnes-image-2.5-flash` | 文生图模型 |
| `AGNES_VIDEO_MODEL` | `agnes-video-2.5-flash` | 文生视频模型 |
| `VIDEO_HUB_PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | 参考图给 Agnes 的公网访问地址 |
| `VIDEO_HUB_AI_MODEL` | `mimo-v2.5` | 字幕分析/解说文案模型 |
| `VIDEO_HUB_WHISPER_MODEL` | `small` | 未配置共享 ASR 时的本地模型 |
| `VIDEO_HUB_ASR_URL` | 无 | 共享 ASR 的 OpenAI 兼容 transcriptions URL；服务器使用 8600 端口 |
| `ASR_IDLE_UNLOAD_SECONDS` | `300` | ASR 空闲多久后卸载 Whisper 模型（释放约 1.3GB 内存） |
| `VIDEO_HUB_NARRATO_TASKS_DIR` | `../narratoai/storage/tasks` | NarratoAI 成片任务目录 |
| `VIDEO_HUB_NARRATO_ENGINE_URL` | `http://127.0.0.1:8601` | 内置解说引擎地址（仅本机） |
| `VIDEO_HUB_NARRATO_ENGINE_TOKEN` | 无 | 引擎可选访问令牌（部署到共享主机时建议设置） |
| `VIDEO_HUB_VISION_BASE_URL` | 回退到 `VIDEO_HUB_AI_BASE_URL` | 视觉模型接口（按画面生成解说） |
| `VIDEO_HUB_VISION_API_KEY` | 回退到 `VIDEO_HUB_AI_API_KEY` | 视觉模型密钥 |
| `VIDEO_HUB_VISION_MODEL` | 无 | 视觉模型名（如 `gpt-4o-mini`、`Qwen2.5-VL`） |
| `NARRATO_MAX_ACTIVE` | `2` | 引擎同时运行的解说任务上限 |
| `VIDEO_HUB_NARRATO_PREVIEW_URL` | `http://127.0.0.1:8502` | 字幕样式预览服务地址 |

AI 分析会把视频字幕和自定义提示发送到所配置的模型服务；不允许字幕外发时不要配置该功能。
配置共享 ASR 后，转写媒体只发送到本机 ASR 服务，不会在 Video Hub 进程重复加载 whisper 模型。

## AI 解说（深度融合）

入口：侧栏「制作 → AI 解说」。这是一套原生页面，不再 iframe Streamlit。

1. **选素材**：直接从视频库选一条（下载、录制、上传、剪辑产生的都可以）；
   在素材库卡片上也能点「AI 解说」直接带着这条视频跳转过来。
2. **定内容**：内容类型可选 通用 / 知识科普 / 纪录片 / 生活记录 / 产品体验 / 教程步骤 / 访谈口播 / 影视剧情 / 自定义；
   默认「通用」，不会把任意视频当成影视解说。
3. **选生成依据**：
   - 按画面理解：抽取关键帧交给视觉模型，适合无声、风光、手工、纪录类；
   - 按口播文稿：粘贴原口播稿或 SRT，模型改写而不是照读，适合访谈、课程、口播；
   - 自动识别口播：调用本机共享 ASR（8600）识别口播再改写，适合没有字幕的访谈/课程；
   - 直接使用解说稿：每行一段，引擎按字数分配时间轴，适合文案已成熟的情况。
4. **配音与合成**：Edge TTS 默认免密钥；可调音色、语速、原声音量、是否烧录字幕；
   引擎负责脚本 → TTS → 裁剪 → 字幕 → 合成，worker 独立进程，可在页面取消。
   - **覆盖范围**默认「精选片段」：从整条视频中挑选片段，时间戳分散在全片，
     不是只从开头连续取；也可切到「跟随原片」，按完整时长生成。
   - **剪辑方式**默认「按原片时间轴」：脚本每一段按自己的 timestamp 区间分别剪切，
     保留原片该段时长；不会再出现 990 秒直播只生成 34 秒成片的问题。
   - 也可切到「按解说长度」：片段时长跟 TTS 走，成片更短更紧凑。
   - **只讲关键点**：提示词要求跳过无关过程，开头点题/设悬念，结尾回扣开头；
     关键画面可以留白。
   - **段间原声停顿**：在相邻解说段之间插入 OST=1 原声片段，实现“暂停解说”，
     不是全程连读。
   - **AI 剪辑（无解说）**：同一套深度分析和选段规划，但全部按原声拼接，
     不生成 TTS/字幕，输出“只听原片”的剪辑版。
   - **渲染前审片**：默认开启。深度分析/脚本规划完成后任务停在“待审片”，
     页面可查看逐段选段、原声停顿和解说词，确认后才开始 TTS、裁剪与合成。
   - **分析缓存**：深度分析按视频指纹 + 分析参数缓存到 `narratoai/storage/analysis_cache`，
     同一素材改脚本、改选段时直接复用，不再重复调用视觉模型。
   - **镜头质量评分**：每个分段计算亮度、对比度、帧差运动量，输出 0–100 分和
     “过暗/过亮/低对比/静止/晃动”标记，写进分段解析并参与全片规划。
   - **成片响度统一**：合成后默认做 EBU R128（-16 LUFS）音频归一化，
     视频流直接复制，只重编码音频。
   - **TTS 读音校正**：重庆/银行/重新/行长/睡觉/长大等常见多音字，
     只在送 TTS 的 `tts_text` 里替换为同音词，字幕仍显示原词。
   - **字幕断句**：按标点和语义切分后再限制每行字数（默认 12，可调 6–24）。
   - **多方案生成**：一次可提交 1–3 个方案（不同叙事节奏/角度），分别审片和低清预览后选择。
   - **AI 素材**：接入 `agnes-image-2.5-flash` 文生图与 `agnes-video-2.5-flash` 文生视频；
     支持图片参考/多图合成，视频支持首尾帧和图片参考；生成图片可保存到图片素材，图片素材可点击选择或拖拽作为参考；生成视频可选择是否加入视频素材库。
     视频队列满时会一直排队重试，生成失败会自动换 Key 重试 3 次，任务可在任务中心取消。
   - **AI Key 池**：文本/视觉/图像/视频共用多 Key 池，遇到 429、401 或 5xx 自动切换并冷却；
     Key 从服务端环境变量读取，不写入前端或仓库。
   - **AI 智能成片**：剪辑页新增按目标时长选段，AI 从整条视频挑选关键片段，
     自动把片段应用到时间线；选段边界会吸附到附近镜头切换点，避免从镜头中间切入切出。
   - **低清选段预览**：审片弹窗里可直接生成 480p 原声预览，
     先检查选段和节奏，再决定是否正式渲染；不生成配音/字幕。
   - **旧版历史任务**：自动扫描旧任务目录里的 `combined.mp4`，可一键导入视频库。
   - **成片直接下载**：完成任务的「下载」按钮通过
     `GET /api/videos/{id}/download` 直接把 MP4 下载到本机；
     不再在服务器生成剪映草稿目录，避免占用服务器空间。
5. **字幕样式**：字体、字号、字色、描边色/宽度、垂直位置（顶/中/底/自定义）、
   遮盖原视频硬字幕（位置/高度/模糊/不透明度）；「预览样式」调用独立字幕预览服务
   （8502）渲染 PNG，不用等整条成片。
6. **自动入库**：成片稳定后由后端复制进视频库，来源标记为「AI 解说」，
   同一任务用 `narrato://task/<id>` 幂等去重；之后可直接剪辑。

**分析深度**默认「深度」：整条视频按 30 秒切段，每段约 1.5 秒抽一帧单独交给视觉模型，
逐段解析结果保存到 `storage/tasks/<task_id>/analysis/segments/` 和 `timeline.md`；
全部段解析完后再统一生成 `plan.json`，决定选哪些片段、如何写解说。
长视频会更慢、视觉模型调用更多；只想要快可以切到「快速」。

模型配置在页面右上「AI 模型」：文本模型写解说，视觉模型看懂画面；
配置写入 `narratoai/config.toml`，API Key 只回传尾号，不返明文。
也可以用 `VIDEO_HUB_AI_*` / `VIDEO_HUB_VISION_*` 环境变量统一管理；环境变量优先于 config.toml。
只使用「文稿 / 解说稿」模式时，可以不配视觉模型。

Edge TTS 已升级到 7.3.1（修复部分区域 `403 Invalid response status`）；如果服务器仍无法访问
`speech.platform.bing.com`，请在 config.toml 配置 Azure / 腾讯云语音，或使用代理。

任务状态：`queued → running → success / failed / cancelled`。
引擎默认最多同时跑 2 个任务，超出的请求会明确返回 429；
取消会杀掉 worker 进程组，不会留下半成品记录。

旧版 Streamlit 服务仍保留用于内部兜底，但公开入口 `/narrato/` 已重定向到新版 `/narrate`；
主链路的解说、进度、入库都不再依赖它。共享 ASR（`/v1/audio/transcriptions`）与
字幕预览服务（`/preview/`）保持不变，仍可被其他模块独立使用。

## 直播录制

抖音直播解析依赖独立的 [DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder)，
不在主仓库内。部署脚本会调用 [deploy/install-dlr.sh](deploy/install-dlr.sh)：

```bash
# 服务器首次/更新
bash deploy/install-dlr.sh
```

systemd 已设置：

```text
VIDEO_HUB_DLR_DIR=/opt/DouyinLiveRecorder
VIDEO_HUB_DLR_PYTHON=/opt/DouyinLiveRecorder/.venv/bin/python
```

Docker Compose 不包含这个外部项目；如需容器部署直播录制，请额外安装并设置
`VIDEO_HUB_DLR_DIR` / `VIDEO_HUB_DLR_PYTHON`。

录制计时规则：创建任务时只显示「正在连接直播流…」，不累计时长；
ffmpeg 真正写出首个媒体字节后才写入 `started_at`，前端「已录」从这个时刻开始算。
直播流解析/握手再慢，也不会被误计为录制时长。

## 参考项目（剪辑思路）

- [PySceneDetect](https://github.com/Breakthrough/PySceneDetect)：内容感知镜头切分
- [ai-montage-agent](https://github.com/zhangzhanglaila/ai-montage-agent)：PySceneDetect + 高光/节奏编排
- [vlog-pipeline](https://github.com/haiiibin/vlog-pipeline)：镜头检测 + 评分 + 剪辑清单

深度分析里的镜头切换检测借鉴这些项目的“先切镜头、再分析、再规划”思路，
但用 ffmpeg 场景分数实现，不新增 OpenCV 依赖；检测失败自动退回固定 30 秒分段。

## 测试

```bash
cd backend && .venv/bin/python -m unittest discover -s tests -v
cd .. && python3 -m unittest discover -s narratoai/engine/tests -v
cd frontend && npm run build
```

## 部署

### systemd + Nginx（当前服务器）

```bash
SSHPASS='服务器密码' ./deploy.sh
```

脚本默认部署到 `root@39.105.112.189:/opt/video-hub`，可用 `SERVER_USER`、`SERVER_IP` 覆盖。
它执行本地测试和前端构建，同步源码，安装 venv 依赖，
启动 `video-hub-backend`、`narrato-engine`、`narratoai`（旧版 Streamlit，保留）、
`narrato-preview`、`narrato-asr`，健康检查通过后切换 Nginx 主入口。密码不会写入脚本。

同步明确排除 `.env`、NarratoAI 真实配置和 storage、数据库、模型及媒体。
首次迁移只复制旧配置和素材，后续部署不覆盖它们；旧 `/opt/NarratoAI` 与
`/opt/narrato-services` 不删除。配置备份在 `/opt/video-hub-backups/<时间戳>`。
需要回滚时恢复该目录内的 Nginx/systemd 配置，执行 `systemctl daemon-reload` 并重启旧服务。

### Docker Compose

`docker-compose.yml` 现在包含 `backend`、`web` 和 `narrato-engine` 三个服务：
引擎与后端共享 `./data`（视频）和 `./narratoai/storage`（任务/成片），
配置持久化在 `narrato-engine-config` 命名卷。首次启动前把模型 Key 写进 `.env`
（`VIDEO_HUB_AI_*` / `VIDEO_HUB_VISION_*`），或在页面「AI 模型」里保存；
容器内写入的是命名卷中的 `config.toml`。共享 ASR 仍通过 `VIDEO_HUB_ASR_URL` 接入。

本项目无认证，只应在可信赖环境使用；不要把无认证 API 暴露给不可信用户。


