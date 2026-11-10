# AI Drama Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 AI 漫剧链路，使普通 AI 视频与漫剧任务解耦，支持 AI 动态分镜、全镜头并发、70 分质量门、持续低分重试和单镜编辑。

**Architecture:** 在现有 FastAPI/React 应用内提取无任务副作用的媒体生成核心。漫剧使用磁盘项目状态派生一个稳定父任务，镜头在项目内运行；存储、规划、流水线、质量检查和 API 分层，前端按项目直接管理镜头。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、threading、SQLite/JSON、FFmpeg、React 18、TypeScript、Vite、现有 unittest/pytest 测试。

**Spec:** `docs/superpowers/specs/2026-11-07-ai-drama-pipeline-design.md`

## Global Constraints

- 基线必须是服务器 `/opt/video-hub` 当前源码，不使用本机旧版 `video-hub` 覆盖。
- 用户选择集数；AI 决定每集镜头数和每镜时长，时长限制为 4–12 秒。
- 图片和视频默认 70 分合格，项目可在 1–100 内调整。
- 低分持续重试，直到合格或手动取消，不设置最大质量重试次数。
- 所有镜头图片合格后，整部作品所有镜头视频同时启动，不主动限制并发。
- 漫剧只有稳定任务 ID `drama-<project_id>`，镜头不进入全局任务中心。
- 不新增微服务、消息队列、数据库或第三方依赖。
- 不覆盖服务器 `.env`、`data`、模型配置和其他运行时状态。
- 真实模型端到端验收前必须单独说明调用规模并取得用户确认。

## File Structure

- Create `backend/app/services/media_generation.py`：纯图片/视频生成，不创建任务。
- Modify `backend/app/services/ai_media.py`：保留现有 API 包装、普通任务和素材入库。
- Create `backend/app/services/media_quality.py`：图片/视频技术检查与视觉评分。
- Create `backend/app/services/drama_store.py`：项目原子持久化、迁移、锁和父任务视图。
- Create `backend/app/services/drama_planner.py`：小说分析、角色与动态分镜。
- Create `backend/app/services/drama_pipeline.py`：阶段编排、全镜头并发、取消和质量重试。
- Modify `backend/app/services/ai_drama.py`：兼容门面，组合 store/planner/pipeline。
- Modify `backend/app/services/job_bridge.py`：合并磁盘漫剧父任务。
- Modify `backend/app/routers/download.py`：按任务类型分发取消。
- Modify `backend/app/routers/drama.py`：新创建参数和单镜接口。
- Modify `frontend/src/api.ts`：新任务、镜头、质量和编辑契约。
- Create `frontend/src/features/drama/ShotEditor.tsx`：单镜编辑。
- Create `frontend/src/features/drama/ShotCard.tsx`：单镜状态、产物与操作。
- Modify `frontend/src/pages/AIDramaPage.tsx`：新创建表单和项目镜头工作台。
- Modify `frontend/src/pages/TasksPage.tsx`、`TasksCard.tsx`、`AIMediaPage.tsx`：类型路由、终态和批量任务。
- Create focused tests under `backend/tests/` for each backend unit.

---

### Task 1: 纯媒体生成核心

**Files:**
- Create: `backend/app/services/media_generation.py`
- Modify: `backend/app/services/ai_media.py`
- Test: `backend/tests/test_media_generation.py`

**Interfaces:**
- Consumes: 现有 `_create_video`、`_poll_video`、`_download`、`_profile_context` 和 Key 池。
- Produces: `GeneratedMedia`、`GenerationCancelled`、`generate_image_asset()`、`generate_video_asset()`；后续漫剧和质量层只调用这些函数。

- [ ] **Step 1: 准备本地依赖并写失败测试**

在隔离副本根目录创建环境，不改服务器：

```bash
uv venv --python 3.12
uv pip install -r backend/requirements.txt pytest
```

pytest 是项目既有文档已经使用的测试运行器，只安装到本地测试环境，不修改服务器运行依赖。

写 `backend/tests/test_media_generation.py`：

```python
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import media_generation


class MediaGenerationTests(unittest.TestCase):
    @patch("app.services.media_generation._image_request")
    def test_image_core_has_no_task_side_effect(self, request):
        request.return_value = media_generation.GeneratedMedia(
            path=Path("image.png"), source_url="", model="test", metadata={}
        )
        with patch("app.services.tasks.task_manager.create") as create:
            result = media_generation.generate_image_asset("一只猫")
        self.assertEqual(result.path, Path("image.png"))
        create.assert_not_called()

    @patch("app.services.media_generation._video_request")
    def test_video_core_has_no_task_or_library_side_effect(self, request):
        request.return_value = media_generation.GeneratedMedia(
            path=Path("video.mp4"), source_url="https://media.test/v", model="test", metadata={}
        )
        with patch("app.services.tasks.task_manager.create") as create, \
             patch("app.db.add_video") as add_video:
            result = media_generation.generate_video_asset("猫转身", 5)
        self.assertEqual(result.path, Path("video.mp4"))
        create.assert_not_called()
        add_video.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_media_generation.py -q
```

Expected: FAIL，无法导入 `app.services.media_generation`。

- [ ] **Step 3: 实现核心数据结构和入口**

`media_generation.py` 至少实现：

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

class MediaAIError(RuntimeError):
    pass


class GenerationCancelled(MediaAIError):
    pass


@dataclass(frozen=True)
class GeneratedMedia:
    path: Path
    source_url: str
    model: str
    metadata: dict[str, Any]


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[float, str], None]


def generate_image_asset(
    prompt: str,
    *,
    size: str = "2K",
    ratio: str = "16:9",
    images: list[str] | None = None,
    should_cancel: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> GeneratedMedia:
    if should_cancel and should_cancel():
        raise GenerationCancelled("生成已取消")
    if progress:
        progress(0, "提交图片生成")
    result = _image_request(prompt, size=size, ratio=ratio, images=images or [], should_cancel=should_cancel)
    if should_cancel and should_cancel():
        result.path.unlink(missing_ok=True)
        raise GenerationCancelled("生成已取消")
    if progress:
        progress(100, "图片已下载")
    return result


def generate_video_asset(
    prompt: str,
    seconds: int,
    *,
    aspect_ratio: str = "16:9",
    first_frame: str = "",
    last_frame: str = "",
    images: list[str] | None = None,
    should_cancel: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> GeneratedMedia:
    if not 4 <= int(seconds) <= 12:
        raise MediaAIError("视频时长必须为 4–12 秒")
    if should_cancel and should_cancel():
        raise GenerationCancelled("生成已取消")
    result = _video_request(
        prompt, int(seconds), aspect_ratio=aspect_ratio,
        first_frame=first_frame, last_frame=last_frame, images=images or [],
        should_cancel=should_cancel, progress=progress,
    )
    if should_cancel and should_cancel():
        result.path.unlink(missing_ok=True)
        raise GenerationCancelled("生成已取消")
    return result
```

将现有上游请求逻辑迁移到 `_image_request()` 和 `_video_request()`，把对 `task.cancelled`、`task.log()` 的依赖替换为 `should_cancel` 和 `progress`。入口负责一次生成；普通任务的三次技术重试保留在 `ai_media.py`，漫剧质量重试由后续流水线负责。

- [ ] **Step 4: 让现有普通 AI 页面使用核心**

保留 `generate_image()` 和 `start_video_generation()` 的现有签名。`ai_media.py` 从核心导入并重新导出 `MediaAIError`，避免循环导入并保持现有调用方兼容。包装层负责 `_profile_context`、任务日志、素材入库和旧一致性行为；核心函数不接收 `task`，不调用 `task_manager.create()` 或 `add_video()`。普通视频任务创建时改为：

```python
task = task_manager.create(
    "ai_video",
    f"AI 视频 · {prompt[:30]}",
    input={"kind": "ai_video"},
)
```

保留旧 `ai` 任务读取兼容，不删除历史任务。

- [ ] **Step 5: 运行新旧媒体测试**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_media_generation.py backend/tests/test_ai_media_quality.py backend/tests/test_narrate_router.py -q
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/media_generation.py backend/app/services/ai_media.py backend/tests/test_media_generation.py
git commit -m "refactor: separate media generation from task lifecycle"
```

---

### Task 2: 统一图片与视频质量评分

**Files:**
- Create: `backend/app/services/media_quality.py`
- Test: `backend/tests/test_media_quality.py`

**Interfaces:**
- Consumes: `GeneratedMedia.path`、现有视觉模型 `_request_json` 的 `vision=True` 调用方式、FFmpeg/ffprobe。
- Produces: `QualityResult`、`QualityUnavailable`、`score_image()`、`score_video()`。

- [ ] **Step 1: 写质量边界失败测试**

```python
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import media_quality


class MediaQualityTests(unittest.TestCase):
    @patch("app.services.media_quality._vision_score")
    @patch("app.services.media_quality._valid_image", return_value=True)
    def test_threshold_is_inclusive(self, _valid, vision):
        vision.return_value = {"score": 70, "reasons": []}
        result = media_quality.score_image(Path("image.png"), "提示词", [], 70)
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 70)

    @patch("app.services.media_quality._vision_score")
    @patch("app.services.media_quality._valid_image", return_value=True)
    def test_low_score_contains_reasons(self, _valid, vision):
        vision.return_value = {"score": 69, "reasons": ["人物服装不一致"]}
        result = media_quality.score_image(Path("image.png"), "提示词", [], 70)
        self.assertFalse(result.passed)
        self.assertEqual(result.reasons, ["人物服装不一致"])

    @patch("app.services.media_quality._valid_image", return_value=False)
    def test_invalid_file_is_zero(self, _valid):
        result = media_quality.score_image(Path("bad.png"), "提示词", [], 70)
        self.assertEqual(result.score, 0)
        self.assertFalse(result.passed)

    @patch("app.services.media_quality._vision_score", side_effect=media_quality.QualityUnavailable("视觉模型不可用"))
    @patch("app.services.media_quality._valid_image", return_value=True)
    def test_unavailable_scorer_does_not_pass(self, _valid, _vision):
        with self.assertRaises(media_quality.QualityUnavailable):
            media_quality.score_image(Path("image.png"), "提示词", [], 70)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 验证测试失败**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_media_quality.py -q
```

Expected: FAIL，模块或接口不存在。

- [ ] **Step 3: 实现评分结果和图片检查**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class QualityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class QualityResult:
    score: int
    passed: bool
    reasons: list[str]
    checker: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "checker": self.checker,
        }


def score_image(path: Path, prompt: str, references: list[str], threshold: int) -> QualityResult:
    if not _valid_image(path):
        return QualityResult(0, False, ["图片不存在、为空或无法解码"], "technical")
    try:
        raw = _vision_score([path], prompt, references, kind="image")
    except QualityUnavailable:
        raise
    except Exception as exc:
        raise QualityUnavailable(str(exc)) from exc
    score = max(0, min(100, int(raw.get("score") or 0)))
    reasons = [str(item) for item in raw.get("reasons") or []]
    return QualityResult(score, score >= threshold, reasons, "vision")
```

`_vision_score(frames: list[Path], prompt: str, references: list[str], kind: str)` 必须要求返回 `score` 和 `reasons`，提示词包含原提示词、参考图和具体评分维度。视觉模型未配置或请求失败时抛 `QualityUnavailable`，不得返回合格。

- [ ] **Step 4: 实现视频技术检查与抽帧评分**

`score_video(path, prompt, reference_image, threshold)` 先用 ffprobe 检查时长、分辨率和可解码性，再抽取开头、中间、结尾各 1 帧和中段连续 5 帧，将帧路径列表交给 `_vision_score(frames, prompt, [reference_image] if reference_image else [], kind="video")`。视频缺失或无法解码时返回 0 分；评分服务异常仍抛 `QualityUnavailable`。

- [ ] **Step 5: 运行质量测试和原有测试**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_media_quality.py backend/tests/test_ai_media_quality.py -q
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/media_quality.py backend/tests/test_media_quality.py
git commit -m "feat: add image and video quality gates"
```

---

### Task 3: 漫剧磁盘存储、迁移与稳定父任务

**Files:**
- Create: `backend/app/services/drama_store.py`
- Modify: `backend/app/services/ai_drama.py`
- Modify: `backend/app/services/job_bridge.py`
- Modify: `backend/app/routers/download.py`
- Test: `backend/tests/test_drama_store.py`
- Test: `backend/tests/test_drama_tasks.py`

**Interfaces:**
- Consumes: 现有 `data/drama/*/project.json` 和 `merged_tasks()`。
- Produces: `task_id_for()`、`normalize_project()`、`load_project()`、`save_project()`、`update_project()`、`task_view()`、`list_task_views()`、`find_project_by_task_id()`。

- [ ] **Step 1: 写稳定任务与旧数据迁移测试**

```python
import unittest

from app.services import drama_store


class DramaStoreTests(unittest.TestCase):
    def test_task_id_is_stable(self):
        self.assertEqual(drama_store.task_id_for("abc123def456"), "drama-abc123def456")

    def test_old_project_gets_schema_and_shot_identity(self):
        old = {
            "id": "abc123def456",
            "title": "旧项目",
            "status": "pending",
            "options": {"episodes_count": 1},
            "episodes": [{"index": 1, "shots": [{"index": 1, "video": ""}]}],
            "characters": [], "logs": [], "created_at": "2026-01-01 00:00:00",
        }
        project = drama_store.normalize_project(old)
        shot = project["episodes"][0]["shots"][0]
        self.assertEqual(project["schema_version"], 2)
        self.assertEqual(project["task_id"], "drama-abc123def456")
        self.assertEqual(project["options"]["quality_threshold"], 70)
        self.assertEqual(shot["id"], "e1s1")
        self.assertEqual(shot["revision"], 1)
        self.assertEqual(shot["image"]["attempts"], 0)
        self.assertEqual(shot["video"]["attempts"], 0)

    def test_task_view_uses_drama_type_and_project_id(self):
        project = drama_store.normalize_project({
            "id": "abc123def456", "title": "项目", "status": "running",
            "options": {}, "episodes": [], "characters": [], "logs": [],
            "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-01 00:00:00",
        })
        view = drama_store.task_view(project)
        self.assertEqual(view["type"], "ai_drama")
        self.assertEqual(view["input"], {"project_id": "abc123def456"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 验证失败**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_drama_store.py -q
```

Expected: FAIL，`drama_store` 不存在。

- [ ] **Step 3: 实现存储和任务视图**

核心接口：

```python
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.config import DATA_DIR

DRAMA_DIR = DATA_DIR / "drama"
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def task_id_for(project_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{12}", project_id):
        raise ValueError("项目编号不合法")
    return f"drama-{project_id}"


def task_view(project: dict[str, Any], *, with_logs: bool = False) -> dict[str, Any]:
    active = project.get("status") in {"pending", "running"}
    view = {
        "id": project["task_id"], "type": "ai_drama", "kind": "ai_drama",
        "title": f"AI 漫剧 · {project.get('title') or project['id']}",
        "status": project.get("status") or "pending",
        "progress": project.get("progress") or 0,
        "input": {"project_id": project["id"]},
        "result": {"project_id": project["id"]},
        "error": {"message": project.get("error")} if project.get("error") else None,
        "created_at": project.get("created_at") or "",
        "finished_at": None if active else project.get("updated_at"),
    }
    if with_logs:
        view["logs"] = list(project.get("logs") or [])[-500:]
    return view
```

`normalize_project()` 必须深拷贝输入，补齐 `schema_version`、`task_id`、`quality_threshold`、镜头 `id/revision/status`、图片/视频产物结构及质量历史。旧 `frame`/`video` 字符串迁移为产物 `name/url`；旧成片保留 `quality=None` 并设置 `legacy_accepted=True`，不得伪造质量分。`save_project()` 使用项目锁和临时文件原子替换；`update_project(project_id, mutator)` 在同一锁内读取、修改并保存。

- [ ] **Step 4: 合并父任务并分发取消**

`merged_tasks()` 合并 `drama_store.list_task_views()` 后再排序和截断；`load_task_view()` 对 `drama-` 前缀读取磁盘项目。`POST /api/tasks/{task_id}/cancel` 对该前缀调用 `ai_drama.cancel_project()`，不得先落入普通内存任务逻辑。

写测试证明连续两次任务视图和续跑使用同一 ID，并证明任务列表不包含 `ai_video` 镜头子任务。

- [ ] **Step 5: 运行存储和任务测试**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_drama_store.py backend/tests/test_drama_tasks.py backend/tests/test_local_access.py -q
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/drama_store.py backend/app/services/ai_drama.py backend/app/services/job_bridge.py backend/app/routers/download.py backend/tests/test_drama_store.py backend/tests/test_drama_tasks.py
git commit -m "refactor: persist one stable task per drama project"
```

---

### Task 4: AI 动态分镜与新创建参数

**Files:**
- Create: `backend/app/services/drama_planner.py`
- Modify: `backend/app/services/ai_drama.py`
- Modify: `backend/app/routers/drama.py`
- Test: `backend/tests/test_drama_planner.py`
- Test: `backend/tests/test_drama_router.py`

**Interfaces:**
- Consumes: `drama_store` 项目结构、现有 `_request_json()`。
- Produces: `PlannedShot`、`plan_novel()`、`plan_episode()`；每镜输出 `id/prompt/seconds/characters/revision/status/image/video`。

- [ ] **Step 1: 写动态镜头测试**

```python
import unittest
from unittest.mock import patch

from app.services import drama_planner


class DramaPlannerTests(unittest.TestCase):
    @patch("app.services.drama_planner._request_json")
    def test_ai_controls_count_and_duration(self, request):
        request.return_value = {
            "shots": [
                {"scene": "客厅", "action": "角色抬头", "seconds": 4, "characters": ["c1"]},
                {"scene": "门口", "action": "角色停下", "seconds": 8, "characters": ["c1"]},
                {"scene": "雨夜", "action": "角色回头", "seconds": 12, "characters": []},
            ]
        }
        shots = drama_planner.plan_episode({"index": 1, "summary": "测试", "source_excerpt": "正文"}, [])
        self.assertEqual(len(shots), 3)
        self.assertEqual([shot["seconds"] for shot in shots], [4, 8, 12])
        self.assertEqual([shot["id"] for shot in shots], ["e1s1", "e1s2", "e1s3"])

    def test_duration_outside_provider_range_is_rejected(self):
        with self.assertRaises(drama_planner.PlannerError):
            drama_planner.validate_shot({"scene": "客厅", "action": "抬头", "seconds": 13})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 验证失败**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_drama_planner.py -q
```

Expected: FAIL，规划模块不存在。

- [ ] **Step 3: 实现规划校验**

```python
from pydantic import BaseModel, Field


class PlannerError(RuntimeError):
    pass


class PlannedShot(BaseModel):
    scene: str = Field(min_length=1)
    action: str = Field(min_length=1)
    expression: str = ""
    camera: str = "固定"
    seconds: int = Field(ge=4, le=12)
    dialogue: str = ""
    characters: list[str] = Field(default_factory=list)


def validate_shot(value: dict) -> PlannedShot:
    try:
        return PlannedShot.model_validate(value)
    except ValueError as exc:
        raise PlannerError("AI 分镜字段或时长不合法") from exc
```

`plan_episode()` 不再接收镜头数或固定秒数。提示词明确要求 AI 根据剧情自主决定镜头数与每镜 4–12 秒时长；空镜头列表抛 `PlannerError`。每镜 `prompt` 由场景、动作、表情和运镜组成，保留原始字段供页面展示。

- [ ] **Step 4: 更新创建模型**

```python
class DramaOptions(BaseModel):
    episodes_count: int = Field(default=2, ge=1, le=10)
    quality_threshold: int = Field(default=70, ge=1, le=100)
    aspect_ratio: str = "9:16"
    style: str = "realistic"
    burn_subtitles: bool = False
```

删除 `shots_per_episode`、`seconds_per_shot`、`max_attempts`。新项目由 store 创建 `schema_version=2` 和稳定任务 ID，规划器写入动态镜头。

- [ ] **Step 5: 运行规划与路由测试**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_drama_planner.py backend/tests/test_drama_router.py -q
```

Expected: PASS，旧固定字段不出现在创建模型中，默认合格线为 70。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/drama_planner.py backend/app/services/ai_drama.py backend/app/routers/drama.py backend/tests/test_drama_planner.py backend/tests/test_drama_router.py
git commit -m "feat: let AI plan dynamic shots and durations"
```

---

### Task 5: 并发漫剧流水线与持续质量重试

**Files:**
- Create: `backend/app/services/drama_pipeline.py`
- Modify: `backend/app/services/drama_store.py`
- Modify: `backend/app/services/ai_drama.py`
- Test: `backend/tests/test_drama_pipeline.py`

**Interfaces:**
- Consumes: Task 1 生成核心、Task 2 质量门、Task 3 store、Task 4 planner。
- Produces: `start_project()`、`resume_project()`、`cancel_project()`、`run_shot()`、`retry_shot()`、`cancel_shot()`、`run_quality_loop()`、`run_video_workers()`、`shot_video_eligible()`；store 新增 `mark_shot_cancelled()`、`mark_shot_failed()`、`mark_project_cancelled()`、`accept_artifact()`、`project_cancelled()`。

- [ ] **Step 1: 写并发和低分重试失败测试**

```python
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import drama_pipeline
from app.services.media_generation import GeneratedMedia, GenerationCancelled
from app.services.media_quality import QualityResult


class DramaPipelineTests(unittest.TestCase):
    @patch("app.services.drama_pipeline._spawn_shot")
    def test_all_shots_start_before_waiting(self, spawn):
        barrier = threading.Barrier(3, timeout=5)

        def spawn_worker(project_id, shot_id, stage):
            worker = threading.Thread(target=barrier.wait)
            worker.start()
            return worker

        spawn.side_effect = spawn_worker
        shots = [{"id": "e1s1"}, {"id": "e1s2"}, {"id": "e1s3"}]
        drama_pipeline.run_video_workers("abc123def456", shots)
        self.assertEqual(spawn.call_count, 3)

    def test_low_scores_retry_until_threshold(self):
        media = GeneratedMedia(Path("shot.mp4"), "", "test", {})
        generate = Mock(return_value=media)
        score = Mock(side_effect=[
            QualityResult(60, False, ["动作不符"], "vision"),
            QualityResult(70, True, [], "vision"),
        ])
        record = Mock()
        result_media, result_quality = drama_pipeline.run_quality_loop(
            prompt="原始提示词", generate=generate, score=score, threshold=70,
            should_cancel=lambda: False, record=record,
        )
        self.assertIs(result_media, media)
        self.assertEqual(result_quality.score, 70)
        self.assertEqual(generate.call_count, 2)
        self.assertIn("动作不符", generate.call_args_list[1].args[0])

    def test_cancelled_loop_does_not_start_another_attempt(self):
        generate = Mock()
        with self.assertRaises(GenerationCancelled):
            drama_pipeline.run_quality_loop(
                prompt="提示词", generate=generate, score=Mock(), threshold=70,
                should_cancel=lambda: True, record=Mock(),
            )
        generate.assert_not_called()

    def test_only_current_qualified_video_can_compose(self):
        shot = {
            "revision": 2,
            "video": {"status": "ready", "revision": 2, "quality": {"passed": True}},
        }
        self.assertTrue(drama_pipeline.shot_video_eligible(shot, 2))
        self.assertFalse(drama_pipeline.shot_video_eligible(shot, 3))
        shot["video"]["quality"]["passed"] = False
        self.assertFalse(drama_pipeline.shot_video_eligible(shot, 2))
        shot["video"]["quality"]["passed"] = True
        shot["video"]["status"] = "stale"
        self.assertFalse(drama_pipeline.shot_video_eligible(shot, 2))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 验证失败**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_drama_pipeline.py -q
```

Expected: FAIL，流水线模块不存在。

- [ ] **Step 3: 实现通用质量循环和每镜 worker**

```python
def run_quality_loop(
    *,
    prompt: str,
    generate: Callable[[str], GeneratedMedia],
    score: Callable[[GeneratedMedia], QualityResult],
    threshold: int,
    should_cancel: CancelCheck,
    record: Callable[[QualityResult, int, str], None],
) -> tuple[GeneratedMedia, QualityResult]:
    reasons: list[str] = []
    attempt = 0
    while not should_cancel():
        attempt += 1
        effective_prompt = prompt
        if reasons:
            effective_prompt += "\n上一版需要修正：" + "；".join(reasons)
        media = generate(effective_prompt)
        if should_cancel():
            media.path.unlink(missing_ok=True)
            raise GenerationCancelled("生成已取消")
        quality = score(media)
        record(quality, attempt, effective_prompt)
        if quality.passed and quality.score >= threshold:
            return media, quality
        reasons = quality.reasons or [f"质量分 {quality.score} 低于 {threshold}"]
    raise GenerationCancelled("生成已取消")


def run_shot(project_id: str, shot_id: str, stage: str) -> None:
    runtime = _register_shot_runtime(project_id, shot_id)
    try:
        if stage in {"image", "all"}:
            _run_image_loop(project_id, shot_id, runtime.cancel)
        if stage in {"video", "all"}:
            _run_video_loop(project_id, shot_id, runtime.cancel)
    except GenerationCancelled:
        drama_store.mark_shot_cancelled(project_id, shot_id)
    except QualityUnavailable as exc:
        drama_store.mark_shot_failed(project_id, shot_id, str(exc), recoverable=True)
    finally:
        _unregister_shot_runtime(project_id, shot_id, runtime)
```

质量循环没有最大尝试次数。`_register_shot_runtime()` 拒绝同一镜同时注册第二个存活 worker。每次接受产物前调用 `accept_artifact(project_id, shot_id, stage, revision, artifact)`；revision 已变化时删除迟到文件并返回 False。临时上游异常使用可被 cancel 中断的递增等待，质量低分立即进入下一次改进生成。

- [ ] **Step 4: 实现全镜头并发和项目取消**

```python
def run_video_workers(project_id: str, shots: list[dict]) -> None:
    workers = [
        _spawn_shot(project_id, shot["id"], "video")
        for shot in shots
    ]
    while workers:
        for worker in list(workers):
            worker.join(timeout=0.2)
            if not worker.is_alive():
                workers.remove(worker)
        if drama_store.project_cancelled(project_id):
            _cancel_all_shots(project_id)


def _run_video_stage(project_id: str) -> None:
    project = drama_store.load_project(project_id)
    shots = [
        shot
        for episode in project["episodes"]
        for shot in episode["shots"]
        if not _shot_video_ready(shot)
    ]
    run_video_workers(project_id, shots)
```

所有 worker 必须在开始 `join` 前完成启动。角色图、镜头图同样经过质量门。只有所有图片合格才进入视频阶段；每集只有 `shot_video_eligible(shot, shot["revision"])` 全部通过才合成。该函数接受当前 revision 且质量合格的产物；仅对迁移时标记 `legacy_accepted=True` 的旧产物允许 `quality=None`，任何编辑使 revision 变化后都清除该豁免。项目取消设置所有 shot cancel event，禁止新尝试。单镜取消使该集进入 `waiting_review`，不合成不完整视频。评分服务不可用则进入可恢复失败，不做无评分合成。

- [ ] **Step 5: 增加项目取消传播测试**

```python
def test_project_cancel_cancels_current_shot(self):
    runtime = SimpleNamespace(cancel=threading.Event())
    with patch.dict(drama_pipeline._SHOT_RUNTIMES, {("abc123def456", "e1s1"): runtime}, clear=True), \
         patch("app.services.drama_pipeline.drama_store.mark_project_cancelled"):
        drama_pipeline.cancel_project("abc123def456")
    self.assertTrue(runtime.cancel.is_set())
```

同时验证续跑沿用 `drama-<project_id>`，取消 shot 不进入质量重试，评分不可用不会调用合成。

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_drama_pipeline.py -q
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/drama_pipeline.py backend/app/services/drama_store.py backend/app/services/ai_drama.py backend/tests/test_drama_pipeline.py
git commit -m "feat: run concurrent drama shots with persistent quality retries"
```

---

### Task 6: 单镜编辑、重试和取消 API

**Files:**
- Modify: `backend/app/services/drama_store.py`
- Modify: `backend/app/services/drama_pipeline.py`
- Modify: `backend/app/services/ai_drama.py`
- Modify: `backend/app/routers/drama.py`
- Test: `backend/tests/test_drama_shot_api.py`

**Interfaces:**
- Consumes: `run_shot()`、项目锁和 revision。
- Produces: `update_shot()`、`retry_shot()`、`cancel_shot()` 及三个 HTTP 接口。

- [ ] **Step 1: 写单镜修改失败测试**

```python
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import app


class DramaShotAPITests(unittest.TestCase):
    @patch("app.routers.drama.ai_drama.update_shot")
    def test_update_only_target_shot(self, update):
        update.return_value = {"id": "e1s1", "revision": 2, "prompt": "新提示词"}
        response = TestClient(app).put(
            "/api/drama/projects/abc123def456/shots/e1s1",
            json={"prompt": "新提示词", "seconds": 8, "reference_images": ["ref_new.png"]},
        )
        self.assertEqual(response.status_code, 200)
        update.assert_called_once_with(
            "abc123def456", "e1s1", prompt="新提示词", seconds=8,
            reference_images=["ref_new.png"],
        )

    @patch("app.routers.drama.ai_drama.retry_shot")
    def test_retry_video_stage(self, retry):
        retry.return_value = {"id": "e1s1", "status": "running"}
        response = TestClient(app).post(
            "/api/drama/projects/abc123def456/shots/e1s1/retry",
            json={"stage": "video"},
        )
        self.assertEqual(response.status_code, 200)
        retry.assert_called_once_with("abc123def456", "e1s1", "video")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 验证失败**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_drama_shot_api.py -q
```

Expected: FAIL，接口返回 404。

- [ ] **Step 3: 实现请求模型和路由**

```python
class ShotUpdateBody(BaseModel):
    prompt: str | None = None
    seconds: int | None = Field(default=None, ge=4, le=12)
    reference_images: list[str] | None = None


class ShotRetryBody(BaseModel):
    stage: Literal["image", "video", "all"] = "all"


def _shot_response(call):
    try:
        return call()
    except ai_drama.DramaError as exc:
        raise _error(exc) from exc


@router.put("/projects/{project_id}/shots/{shot_id}")
def update_shot(project_id: str, shot_id: str, body: ShotUpdateBody):
    return _shot_response(lambda: ai_drama.update_shot(
        project_id, shot_id, **body.model_dump(exclude_none=True)
    ))


@router.post("/projects/{project_id}/shots/{shot_id}/retry")
def retry_shot(project_id: str, shot_id: str, body: ShotRetryBody):
    return _shot_response(lambda: ai_drama.retry_shot(project_id, shot_id, body.stage))


@router.post("/projects/{project_id}/shots/{shot_id}/cancel")
def cancel_shot(project_id: str, shot_id: str):
    return _shot_response(lambda: ai_drama.cancel_shot(project_id, shot_id))
```

- [ ] **Step 4: 实现 revision 失效规则**

更新时在项目锁内只修改目标 shot，递增 revision 并取消旧 worker。图片相关变化清空图片合格状态并把视频标为 `stale`；仅修改时长可直接保留图片，但视频标为 `stale`。已合成集标记 `stale`。`video` 重试必须存在当前合格图片，否则返回 409。

`reference_images` 只保存 `ref_<uuid>` 或 `lib_<uuid>` 文件名，不把大型 data URI 写入项目 JSON。store 校验名称与允许前缀；生成时由流水线从对应参考图目录读取并转换为 data URI，禁止任意路径。

增加测试证明另一镜不变、旧 worker 收到取消、旧 revision 不能覆盖新结果，并断言非法参考图名称返回 400。

- [ ] **Step 5: 运行 API 和流水线回归**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_drama_shot_api.py backend/tests/test_drama_pipeline.py -q
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/drama_store.py backend/app/services/drama_pipeline.py backend/app/services/ai_drama.py backend/app/routers/drama.py backend/tests/test_drama_shot_api.py
git commit -m "feat: edit retry and cancel individual drama shots"
```

---

### Task 7: 漫剧前端单镜工作台

**Files:**
- Modify: `frontend/src/api.ts`
- Create: `frontend/src/features/drama/ShotEditor.tsx`
- Create: `frontend/src/features/drama/ShotCard.tsx`
- Modify: `frontend/src/pages/AIDramaPage.tsx`
- Test: `frontend/package.json` 的现有 `build` 脚本

**Interfaces:**
- Consumes: Task 6 HTTP 接口、现有 `uploadAIReference()`、`ImageLibraryPicker` 和 UI 组件。
- Produces: `DramaShot` 新类型、`updateDramaShot()`、`retryDramaShot()`、`cancelDramaShot()`、`ShotEditor`、`ShotCard`。

- [ ] **Step 1: 更新 API 契约并确认旧页面类型检查失败**

```typescript
export interface DramaQuality {
  score: number
  passed: boolean
  reasons: string[]
  checker: string
}

export interface DramaArtifact {
  name: string
  url: string
  status: 'pending' | 'running' | 'ready' | 'stale' | 'failed' | 'cancelled'
  attempts: number
  quality: DramaQuality | null
  revision: number
}

export interface DramaShot {
  id: string
  index: number
  prompt: string
  scene: string
  action: string
  expression: string
  camera: string
  seconds: number
  dialogue: string
  characters: string[]
  reference_images: string[]
  revision: number
  status: string
  image: DramaArtifact
  video: DramaArtifact
  quality_history: Array<{ stage: string; score: number; reasons: string[]; revision: number; created_at: string }>
}

export function updateDramaShot(projectId: string, shotId: string, payload: {
  prompt?: string
  seconds?: number
  reference_images?: string[]
}): Promise<DramaShot> {
  return http.put<DramaShot>(`/drama/projects/${projectId}/shots/${shotId}`, payload).then(response => response.data)
}

export function retryDramaShot(projectId: string, shotId: string, stage: 'image' | 'video' | 'all'): Promise<DramaShot> {
  return http.post<DramaShot>(`/drama/projects/${projectId}/shots/${shotId}/retry`, { stage }).then(response => response.data)
}

export function cancelDramaShot(projectId: string, shotId: string): Promise<DramaShot> {
  return http.post<DramaShot>(`/drama/projects/${projectId}/shots/${shotId}/cancel`, {}).then(response => response.data)
}
```

`DramaOptions` 删除固定镜头数、固定秒数、最大重试次数，增加 `quality_threshold: number`。`TaskStatus` 增加 `interrupted` 和 `waiting_review`，`Task.input` 与 `Task.result` 增加可选 `project_id: string`，`Task.type` 增加 `ai_video | ai_drama`。旧页面引用已删除字段时构建应失败，用于找出所有需要修改的位置。

Run:

```bash
cd frontend && npm ci && npm run build
```

Expected: FAIL，指出旧字段或旧 shot 字段引用。

- [ ] **Step 2: 实现 ShotEditor**

组件契约：

```typescript
interface ShotEditorProps {
  projectId: string
  shot: DramaShot
  open: boolean
  onClose: () => void
  onSaved: (shot: DramaShot) => void
}
```

表单受控字段为 `prompt`、`seconds`、`reference_images`。保存调用 `updateDramaShot`；照片支持上传和素材库选择；错误使用现有 `ProblemAlert`/`toast`，保存期间禁止重复提交。

- [ ] **Step 3: 实现 ShotCard**

组件契约：

```typescript
interface ShotCardProps {
  projectId: string
  shot: DramaShot
  onEdit: (shot: DramaShot) => void
  onChanged: () => void
}
```

显示集内镜头号、秒数、提示词、图片、视频、两类分数、尝试次数和状态。按钮分别调用 `retryDramaShot(projectId, shot.id, 'image')`、`'video'`、`'all'` 和 `cancelDramaShot()`；低分原因和尝试历史可展开。

- [ ] **Step 4: 重构 AIDramaPage**

创建表单只保留小说、标题、集数、画幅、风格、字幕和默认 70 的合格线。读取 `?project=` 并选中对应项目。项目详情按集渲染 `ShotCard`，打开 `ShotEditor`；轮询只请求漫剧项目接口，不读取普通 AI 视频任务。

- [ ] **Step 5: 构建验证**

Run:

```bash
cd frontend && npm run build
```

Expected: PASS，无 TypeScript 错误。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api.ts frontend/src/features/drama/ShotEditor.tsx frontend/src/features/drama/ShotCard.tsx frontend/src/pages/AIDramaPage.tsx
git commit -m "feat: add per-shot drama editing workspace"
```

---

### Task 8: 任务中心与普通 AI 视频页面收尾

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/TasksCard.tsx`
- Modify: `frontend/src/components/StatusBadge.tsx`
- Modify: `frontend/src/pages/TasksPage.tsx`
- Modify: `frontend/src/pages/AIMediaPage.tsx`
- Modify: `backend/app/services/ai_media.py`
- Test: `backend/tests/test_drama_tasks.py`

**Interfaces:**
- Consumes: `ai_drama`、`ai_video`、旧 `ai` 任务和稳定 `batch_id`。
- Produces: 正确路由、标签、取消行为和整批视频进度。

- [ ] **Step 1: 写后端兼容回归测试**

新增断言：漫剧任务取消调用 `cancel_project()`；普通 `ai_video` 仍走 `task_manager.cancel()`；旧 `ai` 视频仍可读取。批量创建返回的每个任务都具有同一个非空 `batch_id`，重试不创建漫剧镜头全局任务。

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_drama_tasks.py -q
```

Expected: 新增断言 FAIL。

- [ ] **Step 2: 实现批次关联和类型标签**

`start_video_batch()` 创建一个 `batch_id=uuid.uuid4().hex[:12]`，通过包装参数传给每个 `ai_video` 任务的 `input`；返回值中的每个任务也带 `batch_id`。`StatusBadge.tsx` 增加 `interrupted=已中断`、`waiting_review=待处理`，避免新状态显示英文原文。

```typescript
ai: { label: 'AI 生成', color: 'blue', icon: (s) => <Sparkle size={s} /> },
ai_video: { label: 'AI 视频', color: 'blue', icon: (s) => <Sparkle size={s} /> },
ai_drama: { label: 'AI 漫剧', color: 'accent', icon: (s) => <Sparkle size={s} /> },
```

- [ ] **Step 3: 修复任务跳转顺序**

```typescript
if (t.type === 'ai_drama' || t.result?.project_id) {
  const projectId = t.input?.project_id || t.result?.project_id || ''
  navigate(projectId ? `/ai-drama?project=${encodeURIComponent(projectId)}` : '/ai-drama')
  return
}
if (t.type === 'ai' || t.type === 'ai_video') {
  navigate(`/ai-media?task=${encodeURIComponent(t.id)}`)
  return
}
```

该分支必须位于通用 `kind.startsWith('ai')` 之前，漫剧不得进入普通 AI 视频页。

- [ ] **Step 4: 修复普通视频终态和批量展示**

`AIMediaPage` 将 `cancelled` 与 `success/failed/partial` 一样视为终态。收到 `ai_drama` 时显示类型错误提示，不显示“视频已加入素材库”。批量任务使用 `videoTaskIds: string[]` 和整批进度，成功数、失败数、取消数都来自同 `batch_id` 的任务，不再只追踪第一条。

- [ ] **Step 5: 运行后端测试与前端构建**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests/test_drama_tasks.py -q
cd frontend && npm run build
```

Expected: 两项均 PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ai_media.py backend/tests/test_drama_tasks.py frontend/src/api.ts frontend/src/components/TasksCard.tsx frontend/src/components/StatusBadge.tsx frontend/src/pages/TasksPage.tsx frontend/src/pages/AIMediaPage.tsx
git commit -m "fix: route drama tasks separately from AI videos"
```

---

### Task 9: 全量回归、文档和受控部署

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-11-07-ai-drama-pipeline-design.md` 仅记录实际实现差异
- Test: 全部后端、引擎和前端构建

**Interfaces:**
- Consumes: Tasks 1–8 的全部接口。
- Produces: 可回滚的服务器部署和验收记录。

- [ ] **Step 1: 更新用户文档**

README 的 AI 漫剧部分明确写出：AI 决定镜头数与 4–12 秒时长、默认 70 分、低分持续重试、全部视频并发、单镜编辑与取消、任务中心只显示一个父任务。若实际实现与 spec 有差异，先回到设计说明并更新文档，不隐藏差异。

- [ ] **Step 2: 运行完整自动测试**

Run:

```bash
VIDEO_HUB_AUTH_MODE=off PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests engine/tests -q
cd frontend && npm run build
```

Expected: 所有测试 PASS，前端构建退出码 0。任何失败都必须先定位并修复，不跳过。

- [ ] **Step 3: 检查变更范围和 Git 状态**

Run:

```bash
git diff --check
git status --short
git diff --stat 5ad1f48..HEAD
```

确认没有 `.env`、真实 `data`、模型 Key、媒体或无关文件进入提交。

- [ ] **Step 4: 提交最终文档**

```bash
git add README.md docs/superpowers/specs/2026-11-07-ai-drama-pipeline-design.md
git commit -m "docs: document dynamic drama quality pipeline"
```

- [ ] **Step 5: 备份并部署目标文件**

先在服务器创建带时间戳的代码备份，再用 `rsync` 同步本任务修改的源码和测试，不排除或覆盖 `.env`、`data`。前端在本机完成构建，只同步新的 `frontend/dist`。安装依赖仅当 manifest 变化；本计划没有新增依赖。

部署命令必须保持现有文件属主为 `narratoai:narratoai`，重启前运行 `nginx -t`，然后重启 `video-hub-backend` 并检查：

```bash
curl -sf http://127.0.0.1:8000/api/health
curl -sf http://127.0.0.1/api/health
systemctl is-active video-hub-backend nginx
```

Expected: 健康接口返回 `status=ok`，两个服务均为 active。

- [ ] **Step 6: 浏览器验收与回滚准备**

用浏览器验证：创建表单无固定镜头字段；项目详情能打开单镜编辑；任务中心漫剧进入 `/ai-drama`；取消状态停止加载；普通 AI 视频仍可用。测试使用本地假上游或已有产物，不默认触发付费生成。

真实模型端到端验收前，单独向用户说明将创建 1 集测试项目、实际镜头数由 AI 决定、所有镜头会真实生成并持续质检重试，取得明确同意后才执行。若部署验证失败，停止后续付费测试并恢复步骤 5 的代码备份。

- [ ] **Step 7: 最终状态检查**

```bash
git status --short --branch
systemctl is-active video-hub-backend nginx
```

Expected: 本地工作树干净；服务 active。真实模型测试若尚未获批准，只能报告“自动测试与部署验收完成，真实生成验收待批准”，不得宣称完整端到端完成。
