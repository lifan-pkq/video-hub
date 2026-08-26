# Video Hub 视频中枢

一体化视频工作台：抖音视频下载 → 本地视频库 → 一键分发到 抖音 / 小红书 / 快手 / 哔哩哔哩，支持直播录制自动入库。

## 功能

- **仪表盘**（Bento 拼贴）：近 14 天发布趋势图、发布成功率环图、视频/账号/录制统计、进行中任务、最近视频横滚
- **视频库**（海报墙）：3:4 竖版封面卡流，在线播放（支持拖动进度）、搜索、来源筛选、排序、重命名、下载到本地、**视频无损截取**（ffmpeg，发布前轻剪辑）、多选批量删除、本地视频上传导入
- **视频剪辑**（时间线）：类 PR 时间线编辑——多视频片段添加、拖动排序、拖边缘逐段裁剪、顺序预览播放，ffmpeg 服务端拼接渲染（自动统一分辨率/帧率/音轨），异步任务带进度，完成自动入库
- **AI 智能剪辑**：视频库一键「智能剪辑」——faster-whisper 本地语音识别转字幕 → LLM 标出开头寒暄/结尾求关注/中间广告区间 → 时间线确认后导出（LLM 走 OpenAI 兼容协议，环境变量可切 Agnes/DeepSeek/通义/小米 MiMo）
- **视频下载**：粘贴抖音分享口令/链接，自动提取、解析并无水印下载入库（yt-dlp + 抖音 cookie，H.265 自动转码 H.264 保证浏览器可播）；**支持多行批量队列下载**（串行执行防风控）
- **直播录制**：粘贴直播间链接（抖音/B站/直链 m3u8/flv，其他平台走 yt-dlp），ffmpeg 录制，可手动停止，完成/停止后自动入库视频库
- **发布视频**：选择库内视频，填标题/简介/标签，勾选任意多个「平台+账号」一键分发（含哔哩哔哩），支持定时发布，实时日志；发布记录可筛选，**排期视图**总览定时任务
- **任务中心**：下载与发布任务统一实时查看，可展开日志与各平台发布结果；导航角标提示进行中任务数
- **平台账号**：多账号管理——网页扫码登录（抖音/小红书/快手）、cookie 上传（哔哩哔哩走此方式）、校验 cookie、删除账号
- **界面**：影院模式——顶部悬浮胶囊导航（毛玻璃）+ 移动端底部 Dock，**Cmd/Ctrl+K 命令面板**（全局跳转/搜视频），深色/浅色双主题（默认深色），页面切换过渡，404 页

## 架构

```
├── backend/      FastAPI 后端（视频库/下载/发布/账号/录制/统计 API）
├── frontend/     React + Vite + Tailwind 前端（自建组件库，无第三方 UI 框架）
├── publisher/    内置发布引擎（social-auto-upload vendored 副本，含 sau CLI、qr_login.py）
├── deploy/       nginx 配置
└── data/         视频文件、封面、录制中文件、SQLite（运行时生成）
```

发布能力来自内置的 `sau` CLI（subprocess 调用），账号与 cookie 文件一一对应
（`cookies/<平台>_<账号>.json`）。抖音下载依赖抖音 cookie
（playwright storage_state 自动转 Netscape 供 yt-dlp 使用）。
网页扫码登录由 `publisher/qr_login.py` 子进程驱动（引擎的 qrcode_callback 回调取码）。

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

环境变量（默认值面向本地开发，Docker 内已覆盖）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `VIDEO_HUB_DATA_DIR` | `../data` | 数据目录 |
| `VIDEO_HUB_PUBLISHER_DIR` | `../publisher` | 发布引擎目录 |
| `VIDEO_HUB_SAU_BIN` | `publisher/.venv/bin/sau` | sau 命令路径 |
| `VIDEO_HUB_COOKIES_DIR` | `publisher/cookies` | 平台 cookie 目录 |
| `VIDEO_HUB_ACCOUNT` | `default` | 发布账号名 |
| `VIDEO_HUB_XVFB` | 无 DISPLAY 时自动开 | 用 xvfb-run 包裹 sau（服务器必需） |
| `VIDEO_HUB_AI_BASE_URL` | `https://api.xiaomimimo.com/v1` | AI 剪辑的 LLM 接口（OpenAI 兼容） |
| `VIDEO_HUB_AI_API_KEY` | 无 | AI 接口密钥（不配则智能剪辑不可用） |
| `VIDEO_HUB_AI_MODEL` | `mimo-v2.5` | 字幕分析模型 |
| `VIDEO_HUB_WHISPER_MODEL` | `small` | 本地语音识别模型（faster-whisper） |

## 部署（Docker）

```bash
SSHPASS='服务器密码' ./deploy.sh
```

脚本会：本地构建前端 → rsync 代码与 Chromium → 停止并备份删除服务器旧项目 →
`docker compose up -d` → 健康检查。访问 `http://49.234.25.175`。

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
2. 添加 webhook 通道：POST `http://<服务器>/api/sms-code`，
   body `{"token":"<data/sms_token.txt 中的密钥>","content":"{{短信内容}}"}`
3. 添加转发规则匹配抖音验证码短信。此后短信一到自动回填，无需手动输入。
