# Video Hub 视频中枢

一体化视频工作台：视频下载 / AI 解说生成 → 本地视频库 → 剪辑 → 一键分发到 抖音 / 小红书 / 快手 / 哔哩哔哩，支持直播录制自动入库。

## 功能

- **仪表盘**（Bento 拼贴）：近 14 天发布趋势图、发布成功率环图、视频/账号/录制统计、进行中任务、最近视频横滚
- **视频库**（海报墙）：3:4 竖版封面卡流，在线播放（支持拖动进度）、搜索、来源筛选、排序、重命名、下载到本地、**视频无损截取**（ffmpeg，发布前轻剪辑）、多选批量删除、本地视频上传导入
- **视频剪辑**（单轨时间线）：播放头截断、删除左/右侧、复制、精确入出点、拖动排序与拖边裁剪、撤销/重做、时间线缩放、浏览器草稿恢复；FFmpeg 服务端拼接渲染并自动入库
- **AI 智能剪辑**：共享或本地 faster-whisper 转写 + FFmpeg 真实静音检测 + LLM 开头/结尾/广告/重复表达分析；长字幕分块，建议可逐条勾选、编辑、预览和应用，保留手动时间线且支持撤销
- **AI 解说**：内嵌 NarratoAI 完整工作台，生成解说脚本、配音、字幕并合成视频；最终成片稳定后自动以「AI 解说」来源入库，同一任务不会重复导入
- **视频下载**：粘贴抖音分享口令/链接，自动提取、解析并无水印下载入库（yt-dlp + 抖音 cookie，H.265 自动转码 H.264 保证浏览器可播）；**支持多行批量队列下载**（串行执行防风控）
- **直播录制**：粘贴直播间链接（抖音/B站/直链 m3u8/flv，其他平台走 yt-dlp），ffmpeg 录制，可手动停止，完成/停止后自动入库视频库
- **发布视频**：选择库内视频，填标题/简介/标签，勾选任意多个「平台+账号」一键分发（含哔哩哔哩），支持定时发布，实时日志；发布记录可筛选，**排期视图**总览定时任务
- **任务中心**：下载与发布任务统一实时查看，可展开日志与各平台发布结果；活动发布任务跨页/刷新恢复，短信验证可在任意页面继续
- **平台账号**：多账号管理——网页扫码登录、cookie 上传/原子更新、校验 cookie、删除账号
- **界面**：分组侧栏工作台 + 移动端底部 Dock，**Cmd/Ctrl+K 命令面板**，深色/浅色双主题，键盘操作、减少动态效果、加载/空/错误/重试状态，404 页
- **本地模式**：无需登录或访问令牌，打开即用；保留媒体格式校验与任务稳定性保护

## 架构

```
├── backend/
│   ├── app/routers/       API 入口与请求校验
│   ├── app/services/      下载、录制、发布、媒体与任务生命周期
│   └── tests/            标准库 unittest 测试
├── frontend/src/
│   ├── api.ts            统一 HTTP 客户端与 API 类型/方法
│   ├── context/          全局任务恢复、轮询与短信验证
│   ├── features/editor/  片段模型、历史、预览、时间线与 AI 面板
│   ├── components/       跨页面业务组件
│   ├── pages/            路由页面（按需懒加载）
│   └── ui/               基础组件与统一请求状态
├── publisher/            内置发布引擎（social-auto-upload vendored 副本）
├── narratoai/            NarratoAI 0.8.7（MIT）脚本、配音、字幕与视频合成
├── services/             独立字幕预览与共享 faster-whisper ASR
├── deploy/               Nginx、systemd 与服务器安装脚本
└── data/                 视频、封面、录制与 SQLite（运行时生成）
```

统一入口由 Nginx 提供：`/api/` 到 Video Hub 后端，`/narrato/` 到 NarratoAI，
`/preview/` 到字幕预览，其他路径到 React 前端。后端每 15 秒扫描
`narratoai/storage/tasks/<task_id>/combined.mp4`，连续两次大小稳定且可探测后复制入库，
以 `narrato://task/<task_id>` 幂等去重。

发布能力来自内置的 `sau` CLI（subprocess 调用），账号与 cookie 文件一一对应
（`cookies/<平台>_<账号>.json`）。抖音下载依赖抖音 cookie。网页扫码登录由
`publisher/qr_login.py` 子进程驱动。

## 剪辑快捷键与 AI 数据边界

- `S`：在播放头截断；`Delete`：删除选中片段。
- `Space`：播放/暂停；`←/→`：移动 1 秒；`Shift+←/→`：移动 5 秒。
- `Ctrl/Cmd+Z`：撤销；`Ctrl/Cmd+Shift+Z` 或 `Ctrl/Cmd+Y`：重做。
- 时间线和导出标题自动保存到当前浏览器；不包含账号凭据，不上传草稿。
- 静音检测与语音转写在本地执行；只有字幕、重复候选和自定义提示会发送给配置的 LLM 服务。

## 本地开发

```bash
# 后端（Python 3.12）
cd backend && uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn app.main:app --port 8000

# 发布引擎（首次）
cd publisher && uv venv --python 3.12 && uv pip install -e .
patchright install chromium

# 前端（Node 20+）
cd frontend && npm install && npm run dev   # http://127.0.0.1:5173
```

本项目按仅本机使用设计，无需登录或访问令牌，启动后打开页面即可使用。
不要将无认证的 API 直接暴露到公网或其他不可信网络。

环境变量（默认值面向本地开发，Docker 内已覆盖）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `VIDEO_HUB_DATA_DIR` | `../data` | 数据目录 |
| `VIDEO_HUB_DLR_DIR` | `../DouyinLiveRecorder` | 抖音直播解析项目目录 |
| `VIDEO_HUB_DLR_PYTHON` | `DouyinLiveRecorder/.venv/bin/python` | 抖音直播解析项目的 Python |
| `VIDEO_HUB_PUBLISHER_DIR` | `../publisher` | 发布引擎目录 |
| `VIDEO_HUB_SAU_BIN` | `publisher/.venv/bin/sau` | sau 命令路径 |
| `VIDEO_HUB_COOKIES_DIR` | `publisher/cookies` | 平台 cookie 目录 |
| `VIDEO_HUB_ACCOUNT` | `default` | 发布账号名 |
| `VIDEO_HUB_XVFB` | 无 DISPLAY 时自动开 | 用 xvfb-run 包裹 sau（服务器必需） |
| `VIDEO_HUB_AI_BASE_URL` | `https://api.xiaomimimo.com/v1` | AI 剪辑的 LLM 接口（OpenAI 兼容） |
| `VIDEO_HUB_AI_API_KEY` | 无 | AI 接口密钥（不配则智能剪辑不可用） |
| `VIDEO_HUB_AI_MODEL` | `mimo-v2.5` | 字幕分析模型 |
| `VIDEO_HUB_WHISPER_MODEL` | `small` | 未配置共享 ASR 时的本地模型 |
| `VIDEO_HUB_ASR_URL` | 无 | 共享 ASR 的 OpenAI 兼容 transcriptions URL；服务器使用 8600 端口 |
| `VIDEO_HUB_NARRATO_TASKS_DIR` | `../narratoai/storage/tasks` | NarratoAI 成片任务目录 |

AI 分析会把视频字幕和自定义提示发送到所配置的模型服务；不允许字幕外发时不要配置该功能。
配置共享 ASR 后，转写媒体只发送到本机 ASR 服务，不会在 Video Hub 进程重复加载 whisper 模型。

## 测试

```bash
cd backend && .venv/bin/python -m unittest discover -s tests -v
cd frontend && npm run build
```

## 部署

### systemd + Nginx（当前服务器）

```bash
SSHPASS='服务器密码' ./deploy.sh
```

脚本默认部署到 `root@39.105.112.189:/opt/video-hub`，可用 `SERVER_USER`、`SERVER_IP`、
`REMOTE_DIR` 覆盖。它执行本地测试和前端构建，同步源码，安装 venv/Chromium 依赖，
启动 `video-hub-backend`、`narratoai`、`narrato-preview`、`narrato-asr`，健康检查通过后
切换 Nginx 主入口。密码不会写入脚本。

同步明确排除 `.env`、NarratoAI 真实配置和 storage、平台 Cookie、数据库、模型及媒体。
首次迁移只复制旧配置和素材，后续部署不覆盖它们；旧 `/opt/NarratoAI` 与
`/opt/narrato-services` 不删除。配置备份在 `/opt/video-hub-backups/<时间戳>`。
需要回滚时恢复该目录内的 Nginx/systemd 配置，执行 `systemctl daemon-reload` 并重启旧服务。

### Docker Compose

`docker-compose.yml` 仍可用于 Video Hub 前后端的容器部署；通过 `VIDEO_HUB_ASR_URL`
和 `VIDEO_HUB_NARRATO_TASKS_DIR` 接入外部 NarratoAI/共享 ASR。容器部署不包含宿主机
systemd 中的 NarratoAI 服务。

本项目无认证，只应在可信赖环境使用；不要把无认证 API 暴露给不可信用户。

## cookie 维护

方式一（推荐）：在「平台账号」页点「+ 添加账号」，扫码登录， cookie 自动保存。

方式二（手动）：
1. 在本地终端执行 `sau <平台> login --account <账号名> --headed` 扫码登录
   （或使用本仓库 publisher/ 目录下的 sau）
2. 把生成的 `cookies/<平台>_<账号名>.json` 在「平台账号」页面上传即可

抖音视频下载功能同样依赖抖音 cookie。

## 抖音短信验证码

发布抖音视频若触发短信二次验证，页面会自动弹出输入框，手动输入即可。
也可以实现全自动（安卓手机）：
1. 手机安装开源 App「短信转发器 SmsForwarder」
2. 添加 webhook 通道：POST `http://<本机地址>/api/sms-code`，
   body `{"content":"{{短信内容}}"}`（旧的 `token` 字段仍可保留，会被忽略）
3. 添加转发规则匹配抖音验证码短信。此后短信一到自动回填，无需手动输入。
