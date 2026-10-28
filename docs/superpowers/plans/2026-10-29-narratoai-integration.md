# Video Hub 与 NarratoAI 融合实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 Video Hub 为主工作台整合服务器 NarratoAI、字幕预览和共享 ASR，自动导入解说成片，部署到 `39.105.112.189` 并推送 GitHub。

**Architecture:** React SPA 通过 `/narrato/` 嵌入保留完整功能的 Streamlit；FastAPI 后端轮询 NarratoAI 最终成片并幂等入库。所有服务由 systemd 管理，Nginx 统一入口，Video Hub 与 NarratoAI 共用 faster-whisper HTTP 服务。

**Tech Stack:** React 19、TypeScript、Vite、FastAPI、SQLite、Streamlit、faster-whisper、FFmpeg、Nginx、systemd、Python unittest。

**Spec:** `docs/superpowers/specs/2026-10-28-narratoai-integration-design.md`

## Global Constraints

- 保留 Video Hub 当前全部未提交功能，不重置、不覆盖、不做无关重构。
- 服务器目录为 `/opt/video-hub`；真实配置、Cookie、数据库、模型和媒体永不进入 Git 或代码同步。
- NarratoAI 最终文件只认 `storage/tasks/<task_id>/combined.mp4`；来源键固定为 `narrato://task/<task_id>`。
- 后端仅监听 `127.0.0.1`；Nginx 统一 `/api/`、`/narrato/`、`/preview/` 和 SPA 路由。
- 项目已有 unittest 与前端构建，不新增测试框架或前端依赖；所有改动按 TDD 先写失败测试。
- 使用当前会话内联执行，不再请求逐项确认；每完成一个可独立交付的任务即提交对应文件。

---

### Task 1: 纳入 NarratoAI 与本地服务源码

**Files:**
- Create: `narratoai/**`（服务器 `/opt/NarratoAI` 源码，排除运行数据）
- Create: `services/subtitle-preview/main.py`
- Create: `services/asr/main.py`
- Create: `services/requirements.txt`
- Modify: `.gitignore`、`.dockerignore`

**Interfaces:**
- Produces: 可部署的 NarratoAI 源码树、预览服务 `main:app`、ASR 服务 `main:app`。

- [ ] **Step 1: 写部署源码边界失败测试**
在 `backend/tests/test_narrato_integration.py` 新增：

```python
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

class VendorBoundaryTests(unittest.TestCase):
    def test_expected_service_sources_exist(self):
        for relative in ("narratoai/webui.py", "narratoai/LICENSE",
                         "services/subtitle-preview/main.py", "services/asr/main.py"):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_runtime_configuration_is_ignored(self):
        ignored = (ROOT / ".gitignore").read_text()
        for pattern in ("narratoai/config.toml", "narratoai/storage/", "services/.venv/"):
            self.assertIn(pattern, ignored)
        self.assertFalse((ROOT / "narratoai/config.toml").exists())
```

- [ ] **Step 2: 运行并确认失败**
Run: `cd backend && .venv/bin/python -m unittest tests.test_narrato_integration -v`
Expected: FAIL，源码尚不存在。

- [ ] **Step 3: 同步服务器源码并复制已验证服务**
使用 rsync over SSH，排除 `.venv`、`.cache`、`__pycache__`、`storage`、`config.toml*`、`resource/scripts` 和备份文件；从本地 `narrato-services-staging` 复制两个 `main.py`。`services/requirements.txt` 写入：

```text
fastapi==0.162.0
uvicorn[standard]==0.54.0
python-multipart>=0.0.20
moviepy==2.2.1
pillow==11.3.0
faster-whisper==1.2.1
```

`.gitignore` 增加 `narratoai/config.toml*`、`narratoai/storage/`、`narratoai/.cache/`、`narratoai/resource/scripts/`、`services/.venv/`、`models/`；`.dockerignore` 同步敏感边界。

- [ ] **Step 4: 验证源码、许可证与秘密文件**
Run: `cd backend && .venv/bin/python -m unittest tests.test_narrato_integration -v && cd .. && git status --short narratoai services`
Expected: PASS；无 `config.toml`、模型、媒体或密钥文件。

- [ ] **Step 5: 提交源码边界**
`git add .gitignore .dockerignore narratoai services backend/tests/test_narrato_integration.py && git commit -m "feat: vendor NarratoAI services"`

---

### Task 2: Video Hub 接入共享 ASR

**Files:**
- Create: `backend/app/services/asr_client.py`
- Modify: `backend/app/config.py`、`backend/app/services/ai_edit.py`
- Test: `backend/tests/test_asr_client.py`

**Interfaces:**
- Produces: `transcribe_remote(video_path: Path, url: str, log=print) -> list[dict]`，返回与现有 `transcribe` 相同的 `{start,end,text}` 秒级 segments。
- Consumes: `VIDEO_HUB_ASR_URL`；空值保留原有进程内 whisper 回退。

- [ ] **Step 1: 写共享 ASR 失败测试**
覆盖 multipart 请求、segments 转换、空文本过滤、HTTP 错误且不加载本地模型：

```python
@patch("app.services.asr_client.requests.post")
def test_transcribe_remote_returns_normalized_segments(self, post):
    post.return_value.json.return_value = {
        "language": "zh", "duration": 2.5,
        "segments": [{"start": 0, "end": 1.234, "text": " 你好 "},
                     {"start": 1.3, "end": 2.5, "text": " "}],
    }
    post.return_value.raise_for_status.return_value = None
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "a.mp4"
        path.write_bytes(b"video")
        result = transcribe_remote(path, "http://127.0.0.1:8600/v1/audio/transcriptions", log=lambda _: None)
    self.assertEqual(result, [{"start": 0.0, "end": 1.23, "text": "你好"}])
    self.assertEqual(post.call_args.kwargs["data"]["response_format"], "verbose_json")
```

另测 `requests.RequestException` 转成 `AIServiceError`，以及配置远端 URL 时 `_get_model` 不被调用。

- [ ] **Step 2: 运行并确认失败**
Run: `cd backend && .venv/bin/python -m unittest tests.test_asr_client -v`
Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现 HTTP 转写与缓存兼容**
`asr_client.py` 用 `with video_path.open("rb")` 上传 `file`，form 包含 `model=whisper-1`、`language=zh`、`response_format=verbose_json`，`timeout=(10, 1800)`。校验 JSON 为 dict、segments 为 list，逐项转换有限数值并忽略空文本；非 2xx/网络失败抛 `AIServiceError`，响应结构错误抛 `AIResponseError`。

`config.py` 增加：

```python
ASR_URL = os.environ.get("VIDEO_HUB_ASR_URL", "").strip()
NARRATO_TASKS_DIR = Path(os.environ.get(
    "VIDEO_HUB_NARRATO_TASKS_DIR", BASE_DIR.parent / "narratoai" / "storage" / "tasks"
)).resolve()
```

`ai_edit.transcribe` 在缓存未命中后按 `ASR_URL` 分支：配置时调用 `transcribe_remote` 并写回现有缓存，否则执行原 `_get_model()` 路径；日志包含检测语言和时长。

- [ ] **Step 4: 运行新旧测试**
Run: `cd backend && .venv/bin/python -m unittest tests.test_asr_client tests.test_ai_edit -v`
Expected: 全部 PASS；共享服务失败时不隐式加载本地模型。

- [ ] **Step 5: 提交共享 ASR**
`git add backend/app/config.py backend/app/services/ai_edit.py backend/app/services/asr_client.py backend/tests/test_asr_client.py && git commit -m "feat: share local ASR with NarratoAI"`

---

### Task 3: 成片稳定检测与幂等导入

**Files:**
- Create: `backend/app/services/narrato_import.py`
- Modify: `backend/app/db.py`、`backend/app/main.py`
- Test: `backend/tests/test_narrato_import.py`

**Interfaces:**
- Produces: `scan_once(tasks_dir: Path) -> list[dict]`、`importer_loop()`、`get_video_by_source(source_platform: str, source_url: str) -> dict | None`。
- Consumes: `NARRATO_TASKS_DIR`、现有 `probe_video`、`make_cover`、`add_video`。

- [ ] **Step 1: 写导入失败测试**
用临时目录和 mock 媒体函数覆盖：
1. 第一次扫描只记录大小，不导入；第二次大小相同才导入。
2. `merger.mp4` 与 `combined_without_auto_subtitles.mp4` 永远忽略。
3. `source_platform=narrato`、`source_url=narrato://task/<id>` 已存在时不复制、不重复写库。
4. FFprobe 失败保留源文件并返回错误日志，后续可重试。
5. 目标重名时仍使用任务 id 构造唯一文件名。

核心断言：

```python
with patch("app.services.narrato_import.add_video") as add_video:
    self.assertEqual(scan_once(tasks_dir), [])
    self.assertEqual(len(scan_once(tasks_dir)), 1)
    self.assertEqual(scan_once(tasks_dir), [])
self.assertEqual(add_video.call_count, 1)
self.assertTrue(final_source.exists())
```

- [ ] **Step 2: 运行并确认失败**
Run: `cd backend && .venv/bin/python -m unittest tests.test_narrato_import -v`
Expected: FAIL，导入服务不存在。

- [ ] **Step 3: 实现单后台轮询与事务边界**
以模块内 `_observed_sizes` 记录 `(task_id, size)`；`scan_once` 仅遍历一级任务目录的 `combined.mp4`。已入库先跳过；稳定后复制为 `narrato_<safe_task_id>.mp4`，生成封面和元数据，再 `add_video(title=f"AI 解说 {task_id[:8]}", ...)`。失败仅记录日志，不删除源文件。

`db.py` 增加按两个来源字段查询的函数；`main.py` startup 仅在 `NARRATO_TASKS_DIR.is_dir()` 时 `asyncio.create_task(importer_loop())`，循环内用 `asyncio.to_thread(scan_once, NARRATO_TASKS_DIR)`，每 15 秒执行，异常捕获后等待重试。

- [ ] **Step 4: 运行导入与素材测试**
Run: `cd backend && .venv/bin/python -m unittest tests.test_narrato_import tests.test_videos -v`
Expected: 全部 PASS，重复扫描不重复写库。

- [ ] **Step 5: 提交导入服务**
`git add backend/app/db.py backend/app/main.py backend/app/services/narrato_import.py backend/tests/test_narrato_import.py && git commit -m "feat: import NarratoAI videos into library"`

---

### Task 4: 前端「AI 解说」入口与来源标识

**Files:**
- Create: `frontend/src/pages/NarratePage.tsx`
- Modify: `frontend/src/App.tsx`、`frontend/src/nav.ts`、`frontend/src/api.ts`、`frontend/src/index.css`

**Interfaces:**
- Produces: `/narrate` 页面，iframe 同源地址 `/narrato/`；`platformLabel("narrato") === "AI 解说"`。
- Consumes: 现有懒加载、NavItem、页面容器和 RequestState 组件。

- [ ] **Step 1: 写静态集成失败检查**
扩展 `backend/tests/test_narrato_integration.py`，断言 `App.tsx` 包含 `path="/narrate"`，`nav.ts` 同时包含 `/narrate`、`AI 解说`、`制作`，`api.ts` 包含 narrato 标签。

- [ ] **Step 2: 运行并确认失败**
Run: `cd backend && .venv/bin/python -m unittest tests.test_narrato_integration -v`
Expected: FAIL，路由和标签不存在。

- [ ] **Step 3: 实现嵌入页与导航**
`NarratePage` 使用 `useState` 维护 loading/error；iframe `title="NarratoAI AI 解说工作台"`、`src="/narrato/"`、`onLoad` 结束加载。渲染前用 `fetch("/narrato/_stcore/health")` 检查服务，失败显示「AI 解说服务暂不可用」、重试按钮和 `<a href="/narrato/" target="_blank" rel="noreferrer">新标签页打开</a>`。

`App.tsx` 懒加载页面并注册 `/narrate`；`nav.ts` 用 `MagicWand` 图标加入「制作」组；`api.ts` 的 `platformLabel` 增加 narrato 映射；`index.css` 增加 `.narrate-page`（取消常规页面最大宽度/内边距）和 `.narrate-frame`（`width:100%; height:calc(100dvh - 64px); border:0`），移动端保留 Dock 安全空间。

- [ ] **Step 4: 构建与浏览器布局检查**
Run: `cd frontend && npm run build && cd ../backend && .venv/bin/python -m unittest tests.test_narrato_integration -v`
Expected: 构建成功、检查 PASS。后续用浏览器检查桌面和 390px 移动布局，无双重页面滚动或导航遮挡。

- [ ] **Step 5: 提交前端入口**
`git add frontend backend/tests/test_narrato_integration.py && git commit -m "feat: add NarratoAI workspace"`

---

### Task 5: systemd、Nginx 与可复现部署

**Files:**
- Create: `deploy/nginx-host.conf`
- Create: `deploy/systemd/video-hub-backend.service`
- Modify: `deploy/systemd/narratoai.service`、`deploy/systemd/narrato-preview.service`、`deploy/systemd/narrato-asr.service`
- Modify: `deploy.sh`、`docker-compose.yml`、`README.md`
- Test: `backend/tests/test_deployment_security.py`

**Interfaces:**
- Produces: 一套幂等部署脚本，同步代码、安装依赖、保留数据、健康检查后切换 Nginx。
- Consumes: `SSHPASS` 环境变量，不在脚本中保存密码；`SERVER_IP` 默认 `39.105.112.189`，`SERVER_USER=root`，`REMOTE_DIR=/opt/video-hub`。

- [ ] **Step 1: 写部署失败测试**
断言脚本不含明文密码和 `StrictHostKeyChecking=no`，保留 `sshpass -e`、运行数据 excludes、`curl -sf http://localhost/api/health` 和重试；Nginx 配置包含三个代理路径及 `$http_upgrade`；NarratoAI unit 包含 `--server.baseUrlPath=narrato`，后端 unit 包含 `VIDEO_HUB_ASR_URL=http://127.0.0.1:8600/v1/audio/transcriptions`。

- [ ] **Step 2: 运行并确认失败**
Run: `cd backend && .venv/bin/python -m unittest tests.test_deployment_security -v`
Expected: FAIL，host 配置尚不存在。

- [ ] **Step 3: 实现服务配置和部署流程**
后端 unit 使用 `/opt/video-hub/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`，设置数据、发布引擎、Cookie、共享 ASR、NarratoAI tasks、`PLAYWRIGHT_BROWSERS_PATH=/opt/video-hub/ms-playwright` 与 `VIDEO_HUB_XVFB=1`。NarratoAI 继续使用 8501 和自己的 venv；预览和 ASR 使用 `/opt/video-hub/services/.venv` 与现有模型路径。

`deploy.sh` 顺序固定：本地测试/构建 → rsync（排除 `.git`、`.env`、全部运行数据与依赖）→ 远端备份配置 → venv/pip/uv 安装 → Chromium 检查 → systemd daemon-reload → 启动并检查 8000/8501/8502/8600 → `nginx -t` → 安装并 reload Nginx → 公网路径健康检查。任何一步失败即停止，不删除 `/opt/NarratoAI`、`/opt/narrato-services` 或 `/opt/video-hub/data`。

`docker-compose.yml` 仅增加 `VIDEO_HUB_ASR_URL` 和 `VIDEO_HUB_NARRATO_TASKS_DIR` 可选环境配置；README 更新融合架构、路由、systemd 部署和回滚步骤。

- [ ] **Step 4: 验证部署静态契约**
Run: `cd backend && .venv/bin/python -m unittest discover -s tests -v && cd .. && nginx -t -c "$PWD/deploy/nginx-host.conf" -p /tmp/video-hub-nginx-test`（若本机无 Nginx，则用 Docker 或服务器的 `nginx -t` 验证，不跳过）。
Expected: 测试与配置检查全部 PASS。

- [ ] **Step 5: 提交部署实现**
`git add deploy deploy.sh docker-compose.yml README.md backend/tests/test_deployment_security.py && git commit -m "feat: deploy unified Video Hub stack"`

---

### Task 6: 全量本地验证与服务器迁移

**Files:**
- Modify only files required by failures discovered during verification; each fix retains its corresponding test.

**Interfaces:**
- Consumes: Tasks 1–5 的全部服务、测试与部署脚本。
- Produces: 可通过公网 Nginx 使用的统一 Video Hub。

- [ ] **Step 1: 跑标准本地验证**
Run:

```bash
cd backend && .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app
cd ../frontend && npm run build
cd ../narratoai && python -m unittest discover -p 'test_*_unittest.py'
```

Expected: 全部通过；若 NarratoAI 需要其依赖，则在该目录创建被忽略的 `.venv` 后按 `pyproject.toml` 安装再跑，不改测试含义。

- [ ] **Step 2: 备份并执行部署**
在远端用带时间戳的 tar 备份 `/etc/nginx/sites-enabled/narratoai`、相关 systemd units、`/opt/NarratoAI/config.toml`；不读取或输出密钥。执行 `SSHPASS=... ./deploy.sh`，保留旧目录作为回滚。

Expected: 四个服务 active，Nginx 配置有效，`/` 返回 Video Hub，`/api/health`、`/narrato/_stcore/health`、`/preview/health` 均成功。

- [ ] **Step 3: 做浏览器与 API 端到端验证**
用 Playwright 打开 `http://39.105.112.189`，检查导航「AI 解说」、iframe 加载、桌面/390px 布局、素材库和账号页。用已有测试音频 POST `/v1` 不公开 ASR，改为在服务器回环验证；通过 `/preview/` 上传测试视频并验证 PNG。所有失败先修复再重跑。

- [ ] **Step 4: 真实生成并验证自动入库**
在 NarratoAI 页面上传一段 4–10 秒自有测试视频，选择现有 edge_tts 和已配置 LLM，生成短视频。等待 `combined.mp4` 完成和两次稳定扫描，确认素材库只新增一个 `narrato` 来源条目，可播放、可进入剪辑和发布页。再次扫描与重启后端后数量不增加。

- [ ] **Step 5: 提交验证修复并检查回滚资产**
仅提交实际验证中产生的修复，运行对应测试。确认备份存在、旧目录未删除、部署未覆盖线上配置和媒体。

---

### Task 7: 文档、GitHub 与交付

**Files:**
- Modify: `README.md`（最终实际命令和验证结果只写可复现信息）
- Modify: `.gitignore`、`.dockerignore`（若验证发现遗漏）

- [ ] **Step 1: 最终敏感信息与仓库检查**
Run:

```bash
git status --short
git diff --check
git ls-files | grep -E '(^|/)(\.env|config\.toml|cookies|data|models|storage)(/|$)' && exit 1 || true
git grep -nE 'sshpass -p|api_key = "sk-[A-Za-z0-9]+' -- . && exit 1 || true
```

Expected: 无密钥、真实配置、模型或媒体；不把命令中的敏感测试值写入仓库。

- [ ] **Step 2: 全量回归**
再次运行 Task 6 Step 1 的 Video Hub 测试、compileall、前端构建和 NarratoAI 测试，并 curl 线上四个健康路径。

- [ ] **Step 3: 提交剩余既有功能与整合改动**
`git add -A` 前逐项检查 `git status` 与 `git diff --stat`，只包含源码、测试、文档和部署文件；提交 `feat: unify video creation and publishing workspace`。

- [ ] **Step 4: 推送并核对 GitHub**
Run: `git push origin main && git status --short --branch && git log -3 --oneline`
Expected: 推送成功，工作区无未提交源码；GitHub `origin/main` 指向最终提交。

- [ ] **Step 5: 交付结果**
最终消息列出访问入口、新增能力、自动入库行为、测试结果、线上验证结果、提交 hash、备份位置及无认证公网部署的安全提醒。
