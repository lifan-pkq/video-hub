# NarratoAI 解说引擎（headless）

Video Hub 的 AI 解说不再通过 Streamlit iframe 驱动。本目录把 NarratoAI 的脚本、TTS、
裁剪、字幕与合成能力封装成一个**本地 JSON API + 隔离 worker**：

```
React -> Video Hub /api/narrate -> engine :8601 -> worker -> storage/tasks/<id>/combined.mp4
```

## 启动

```bash
cd narratoai
cp -n config.example.toml config.toml
.venv/bin/python -m engine.server --host 127.0.0.1 --port 8601
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `NARRATO_ENGINE_HOST` | `127.0.0.1` | 监听地址，不要暴露公网 |
| `NARRATO_ENGINE_PORT` | `8601` | 端口 |
| `NARRATO_ENGINE_TOKEN` | 空 | 可选，请求头 `X-Engine-Token` |
| `NARRATO_MAX_ACTIVE` | `2` | 同时运行的 worker 数 |
| `NARRATOAI_CONFIG` | `<root>/config.toml` | 配置文件路径 |
| `VIDEO_HUB_AI_*` / `VIDEO_HUB_VISION_*` | 空 | 模型配置兜底；设置后优先于 config.toml |

## API

- `GET /health`：进程与依赖状态
- `GET /capabilities`：内容类型、音色、画幅、脚本模式、模型配置（脱敏）
- `GET /settings` / `PUT /settings`：读写 config.toml 中的文本/视觉/TTS 字段
- `POST /tasks`：创建任务，立即返回 202
- `GET /tasks/{id}?logs=1`：状态、进度、结果
- `POST /tasks/{id}/cancel`：杀掉 worker 进程组
- `GET /tasks/{id}/script`：查看生成的解说脚本
- `GET /tasks`：最近任务

### 创建任务

```json
{
  "task_id": "narrate-abc123",
  "video_path": "/opt/video-hub/data/videos/xxx.mp4",
  "video_width": 1080,
  "video_height": 1920,
  "video_duration": 42.5,
  "title": "演示",
  "content_type": "knowledge",
  "topic": "咖啡萃取",
  "instruction": "讲给完全不懂的人听",
  "target_duration": 60,
  "language": "中文",
  "mode": "auto",
  "clip_mode": "timeline",
  "coverage": "highlights",
  "analysis_mode": "deep",
  "output_mode": "narration",
  "pause_between": true,
  "pause_seconds": 0.4,
  "aspect": "9:16",
  "voice_name": "zh-CN-YunxiNeural",
  "tts_engine": "edge_tts",
  "voice_rate": 1.05,
  "original_volume": 0.15,
  "subtitles": true
}
```

`mode` 取值：

- `auto`：有 `script_items` / `script_text` 用文稿，否则按画面理解；
- `visual`：抽帧 + 视觉模型；
- `transcript`：使用 `transcript_text`（口播稿/SRT）改写；
- `asr`：未提供文稿时调用共享 ASR（`VIDEO_HUB_ASR_URL`，默认 8600）识别口播再改写；
- `script`：直接使用 `script_items` 或按行分配 `script_text`。

`review_mode`：默认 `true`，脚本/规划完成后任务进入 `awaiting_review`，
确认后由 `POST /tasks/{id}/approve` 从 `script.json` 继续渲染。

`tts_pronunciation`：默认 `true`，给每段写入 `tts_text`（同音词），
TTS 用 `tts_text`，字幕仍用原 `narration`。

`subtitle_max_chars`：每行字幕最大字数，默认 16，范围 6–30。

`output_mode`：

- `narration`：正常解说 + TTS + 字幕；
- `edit`：只按选段计划拼原片，不生成 TTS/字幕。

`pause_between` / `pause_seconds`：相邻解说段之间插入多长的 OST=1 原声停顿。

`analysis_mode` 取值（默认 `deep`）：

- `deep`：整片按镜头切换点/30 秒切段，每段 1.5 秒抽帧单独解析；
  结果落盘后再统一规划；
- `fast`：对整条视频做一次抽帧分析。

深度模式产物：

```text
storage/tasks/<task_id>/analysis/
  analysis.json          分段参数与失败段
  timeline.md            带全局时间戳的全片解析
  segments/segment_0001.json
  segments/segment_0001.analysis.json
  plan.json              最终选段与解说
  segments/*.json        每段解析 + quality 质量评分
```

`coverage` 取值（默认 `highlights`）：

- `highlights`：从整条视频中挑选片段，timestamp 分布在全片，适合 60 秒精选；
- `full`：覆盖完整原片，目标时长自动取源视频时长。

`clip_mode` 取值（默认 `timeline`）：

- `timeline`：按每段 timestamp 区间剪切，保留原片该段时长；TTS 比区间短时保留原声画面，
  不会只取视频开头几十秒；
- `narration`：片段时长跟随 TTS，成片更短更紧凑。

任务目录结构：

```
storage/tasks/<task_id>/
  spec.json     归一化输入
  state.json    进度/状态（worker 原子写）
  script.json   解说脚本（narration/timestamp/OST/_id）
  result.json   成片路径与尺寸
  worker.log    子进程日志
  combined.mp4  最终成片
```

## 内容通用性

- 9 个内容 Profile 在 `engine/profiles.py`，默认 `auto`，不绑定影视解说。
- 脚本提示词在 `engine/script.py`，按 Profile 注入风格，不虚构画面/文稿外的信息。
- 视觉分析复用 `DocumentaryFrameAnalysisService.analyze_video`，但文案不再用固定的
  “病毒式影视解说”模板。
- TTS/裁剪/字幕/合成复用 NarratoAI 的 `task.start_subclip_unified`；进度通过
  `PipelineState` 转入 `state.json`，与 Streamlit 完全解耦。

## 测试

```bash
python3 -m unittest discover -s narratoai/engine/tests -v
```
