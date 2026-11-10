# 剪辑台

Video Hub 里「智能剪辑」和「AI 解说」的重做。两条线都先给可核对的中间结果，再谈模型。

- **智能剪辑** `/edit`：从素材库加入成片，用 ffmpeg 测静音、黑场和镜头，用转写时间标停顿和口头禅。时间线上接受或拒绝，再按保留段导出。模型只对已有候选做删/留，不发明时间段。
- **AI 解说** `/narrate`：理解（转写 + 抽帧）→ 文稿 → 审阅 → 渲染（配音、字幕、按稿切）。每一步都能改。缺 ASR、缺模型、配音对不上画面，都会写出原因和下一步，不会交回空结果。

本机使用，没有登录。不要把无认证接口暴露到公网。

## 本地运行

需要 Python 3.12、Node 20+、ffmpeg。

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 可选：pip install edge-tts faster-whisper
cd ..
VIDEO_HUB_DATA_DIR=./data VIDEO_HUB_JOBS_INLINE=0 \
  backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765

cd frontend
npm install
npm run dev
```

打开 http://127.0.0.1:43123/edit 。前端把 `/api` 代理到 8765。素材库为空时点「演示素材」。

环境变量见 `.env.example`。密钥只放环境变量，不进仓库。

## 测试

```bash
backend/.venv/bin/python -m pytest
cd frontend && npm run build
```

## 接到现有 Video Hub

看 [DEPLOY.md](DEPLOY.md)。推荐用独立端口跑这一套，把 `VIDEO_HUB_EXTERNAL_DB` 指到现有库，这样不会改掉其它页面。
