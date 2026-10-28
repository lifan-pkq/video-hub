# Video Hub 与 NarratoAI 融合设计

## 目标与范围
以 Video Hub 为统一工作台，将当前服务器上的 NarratoAI 0.8.7、字幕预览服务和 faster-whisper ASR 服务纳入同一代码仓库与部署体系。保留 NarratoAI 成熟的 Streamlit 生成界面，新增 Video Hub「AI 解说」入口，并让最终成片自动进入 Video Hub 素材库供剪辑、下载和多平台发布。

目标服务器为 `39.105.112.189`，线上目录为 `/opt/video-hub`。现有 Video Hub 剪辑、AI 分析及其他未提交改动全部保留。不重写 NarratoAI 的脚本、配音、字幕和合成引擎，不把生成任务重复接入 Video Hub 任务中心。

## 服务边界
- `web`：Nginx 监听 80，提供 Video Hub 静态前端并统一反向代理。
- `video-hub-backend`：FastAPI，监听 `127.0.0.1:8000`，处理素材、剪辑、发布、账号、下载、录制和 NarratoAI 成片导入。
- `narratoai`：Streamlit，监听 `127.0.0.1:8501`，保留现有生成工作台。
- `narrato-preview`：字幕预览 FastAPI 服务，监听 `127.0.0.1:8502`。
- `narrato-asr`：共享 faster-whisper 服务，监听 `127.0.0.1:8600`，继续提供 OpenAI 兼容转写接口。

路由固定为：`/api/` 到 Video Hub 后端，`/narrato/` 到 NarratoAI，`/preview/` 到字幕预览，其余路径交给 Video Hub React SPA。NarratoAI 使用 Streamlit `baseUrlPath=narrato`，Nginx 保留其 WebSocket 升级请求头。所有后端只监听回环地址，不单独暴露端口。

## 前端与使用流程
- Video Hub 侧栏「制作」分组新增「AI 解说」，路由为 `/narrate`，加入命令面板和移动端更多页面。
- 页面保留 Video Hub 导航和顶栏，内容区通过同源 iframe 加载 `/narrato/`；提供加载状态、服务不可用错误及在新标签页打开入口。
- 素材来源筛选增加「AI 解说」。导入成片沿用现有视频卡片、播放、剪辑、下载和发布流程，不新增另一套素材页面。
- NarratoAI 的生成进度和错误留在其工作台内。Video Hub 任务中心只显示自己的任务及导入错误日志，不伪造 NarratoAI 任务状态。

## 成片自动导入
NarratoAI 的最终产物为 `storage/tasks/<task_id>/combined.mp4`，中间文件如 `merger.mp4`、`combined_without_auto_subtitles.mp4` 不参与导入。

Video Hub 后端增加独立导入服务，轮询 `VIDEO_HUB_NARRATO_TASKS_DIR` 指向的任务目录。仅当最终文件存在、非空、可被 FFprobe 读取且连续两次轮询大小不变时才导入。导入时复制到 Video Hub 素材目录，使用任务 id 生成不冲突的文件名，探测时长和分辨率并生成封面，再以 `source_platform=narrato`、`source_url=narrato://task/<task_id>` 写入现有素材表。

导入前按 `source_platform + source_url` 查询，确保同一 NarratoAI 任务跨扫描和服务重启只入库一次。复制或入库失败不删除源文件，记录日志并在后续扫描重试；已完成任务不再因临时错误重复导入。历史成片按相同规则导入。扫描间隔为 15 秒，不新增文件监听依赖。

## 共享 ASR 与字幕预览
Video Hub AI 剪辑的转写增加 `VIDEO_HUB_ASR_URL` 配置。服务器设置为 `http://127.0.0.1:8600/v1/audio/transcriptions`，上传本地音频/视频并请求 `verbose_json`，将返回 segments 转换为现有转写结构。这样 Video Hub 与 NarratoAI 共用已加载的 whisper-small 模型，避免重复占用内存。

本地开发未配置 `VIDEO_HUB_ASR_URL` 时保留原有 faster-whisper 进程内转写作为回退。远端 ASR 失败时返回明确错误，不静默加载第二份模型。字幕预览继续通过 `/preview/` 提供遮罩与样式接口，不修改其已验证行为。

## 仓库与配置
将服务器 NarratoAI 源码及 MIT 许可证纳入根目录 `narratoai/`，字幕预览与 ASR 服务源码分别纳入 `services/subtitle-preview/` 和 `services/asr/`。NarratoAI 的 `config.example.toml` 作为模板；真实 `config.toml` 由服务器保留和迁移。

Git 与部署均排除 `.env`、真实配置、API Key、Cookie、数据库、模型、生成视频、依赖目录、缓存和构建产物。部署脚本不写入密码；SSH 凭据只从执行环境获取。现有 Video Hub 本地 `publisher/` 及未提交功能代码保持原样纳入最终验证。

## 部署与运行环境
采用 `venv + systemd + Nginx`，与当前服务器运行方式一致；保留 Docker Compose 供其他环境使用，不在本次部署中强制迁移到 Docker。

代码位于 `/opt/video-hub`，Video Hub 与发布引擎共用 `/opt/video-hub/.venv`，Chromium 位于 `/opt/video-hub/ms-playwright`。NarratoAI 使用自己的 `/opt/video-hub/narratoai/.venv`。真实配置、`storage`、素材和账号目录在代码同步时排除并持久保留。服务器安装 FFmpeg、Xvfb 和 Chromium 所需系统依赖。

现有 `/opt/NarratoAI` 和 `/opt/narrato-services` 在切换前保留作为回滚来源。新服务通过健康检查后，才将 Nginx 主入口从 NarratoAI 切换到 Video Hub；旧 NarratoAI 配置、Nginx 配置和 systemd 单元预先备份。部署失败不删除旧目录、不清空数据，恢复旧 Nginx 与 systemd 配置即可回滚。

## 错误处理与资源约束
- 任一生成、转写或导入步骤失败都保留源文件并输出可操作错误。
- 后端导入扫描为单后台任务，避免并发重复导入；转写按现有 AI 剪辑任务串行执行。
- ASR 服务保留 1.5G 内存限制；Video Hub 配置共享 ASR 后不加载本地 whisper 模型。
- 日志使用 systemd journal，保留服务重启策略；不在日志中记录 Cookie、API Key 或完整认证配置。

## 验证与交付
本地依次通过 Video Hub 后端 unittest、compileall、前端生产构建、NarratoAI 项目测试，以及导入稳定性/去重、共享 ASR 响应转换测试。前端无既有浏览器测试框架，不新增测试依赖。

线上验证首页与 API、AI 解说 iframe、字幕预览、ASR 转写、素材库和账号页。实际生成一条短视频，确认只导入一次、可播放、可进入剪辑和发布流程。检查服务重启后状态与 Nginx 路径，确认媒体与配置未被部署覆盖。

最后将当前 Video Hub 改动与融合实现提交并推送到 `https://github.com/lifan-pkq/video-hub.git`。GitHub 不包含真实服务器配置、密钥、账号数据、模型或媒体文件。
