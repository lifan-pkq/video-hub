# AI 解说原生融合设计（2026-10-29）

## 背景

旧方案把 NarratoAI 的 Streamlit 页面用 iframe 嵌进 `/narrate`，后端只扫描
`storage/tasks/<id>/combined.mp4` 入库。这带来三个问题：

1. 两套 UI、两套任务状态，选素材/看进度/取消都要跳进 Streamlit；
2. 解说不是 Video Hub 的一等公民，任务中心、视频库、发布链路无法感知；
3. NarratoAI 自带提示词偏“影视/短剧病毒式解说”，不适合知识、教程、访谈、生活等通用内容。

## 目标

- 一个前端：AI 解说完全在 React 工作台内完成；
- 一套任务：SQLite 记录、状态可恢复、可取消、可重试入库；
- 通用内容：内容 Profile + 画面/口播/现成稿三种脚本来源；
- 保留 NarratoAI 的 TTS/裁剪/字幕/合成能力，不改写其媒体管线；
- ASR、字幕预览、发布、剪辑等既有服务不受影响。

## 架构

```
React NarratePage
  -> /api/narrate/*（FastAPI，SQLite narration_tasks）
  -> narrato-engine :8601（stdlib HTTP，独立进程）
  -> worker 子进程（narratoai/.venv）
     -> 内容 Profile / 视觉分析 / 文稿改写
     -> task.start_subclip_unified（TTS、裁剪、字幕、合成）
  -> storage/tasks/<id>/combined.mp4
  -> narrato_import.import_task（统一媒体管线：封面/探测/幂等）
```

### 为什么 Engine 仍是独立进程

- NarratoAI 依赖 moviepy/streamlit/edge-tts 等重依赖，和 FastAPI 后端环境分离；
- 每个生成任务用独立 worker，可取消（杀进程组）、崩溃隔离、并发上限 2；
- 进度写 `state.json`，Engine/后端重启后仍可恢复展示。

### 契约

- Engine 只监听 `127.0.0.1:8601`，Nginx 不暴露；可选 `X-Engine-Token`。
- 后端是唯一调用方；前端只认 `/api/narrate/*`。
- 成片路径以共享任务目录 `storage/tasks/<id>/combined.mp4` 为准，兼容不同容器挂载点。
- 来源键 `narrato://task/<id>` 保证重复扫描/重试不会重复入库。

## 数据模型

`narration_tasks`：id、video_id、title、content_type、mode、aspect、voice_name、
target_duration、status、progress、message、error、engine_task_id、output_video_id、
request_json、result_json、created_at/updated_at/finished_at。

状态：`queued → running → success / failed / cancelled`。

## 内容通用性

- Profile：auto / knowledge / documentary / vlog / product / tutorial / interview / film / custom，
  默认 auto，不预设影视。
- 脚本来源：visual（抽帧）、transcript（口播稿/SRT）、script（现成解说稿）。
- 提示词只允许使用画面/文稿里的事实，禁止虚构；按镜头顺序、控制时长与句长。
- 画幅默认跟随原片（9:16 / 16:9 / 1:1 / 3:4）。

## 失败与取消

- worker 异常写 failed + traceback；Engine 重启把死进程任务收口为 failed。
- 取消杀 worker 进程组（SIGTERM → 5s → SIGKILL），本地标记 cancelled。
- 成片已生成但入库失败时保留 success + 错误，页面提供“重试入库”。

## 兼容

- 旧 Streamlit `/narrato/` 继续运行，仅作为旧工作台；主链路不依赖。
- 旧任务仍会被扫描入库，来源标识一致。
- ASR（8600）、字幕预览（8502）、发布引擎（sau）不改。

## 部署

- systemd 增加 `narrato-engine.service`；后端环境变量 `VIDEO_HUB_NARRATO_ENGINE_URL=http://127.0.0.1:8601`。
- Docker Compose 增加 `narrato-engine`，与 backend 共享 `./data`、`./narratoai/storage`，配置存命名卷。
- 健康检查：`/api/health`、`:8601/health`、`/preview/health`、`:8600/health`、`/narrato/_stcore/health`（旧）。

## 测试

- Engine：`narratoai/engine/tests`（spec/profile/store/settings/脚本解析）。
- 后端：`tests/test_narration*.py`（建任务、入库、脱敏、DB）、旧有导入/部署测试。
- 前端：`npm run build`（TS 严格模式）。
