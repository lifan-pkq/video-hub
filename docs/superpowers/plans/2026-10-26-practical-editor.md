# 实用剪辑与智能分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为单轨编辑器增加精确截断、历史/草稿和可控制的真实 AI 建议。

**Architecture:** 前端以纯函数片段模型为中心，历史、预览、AI 面板和时间线分离；后端以本地音频分析 + 分块转写 LLM 为数据来源。现有 `/api/edit/concat` 不变，`analyze` 向后兼容扩展。

**Tech Stack:** React 18、TypeScript、Tailwind 3、现有 timeline editor、FastAPI、FFmpeg、faster-whisper、unittest、Playwright。

**Spec:** `docs/superpowers/specs/2026-10-26-practical-editor-design.md`

## Global Constraints
- 不增加依赖，不覆盖原视频，不上传视频文件给 LLM。
- 最短片段 0.3 秒，所有编辑操作重新 pack 且源区间合法。
- 保留现有用户改动，不提交 Git，不触发真实发布。
- 前端没有测试框架，使用浏览器验收，不新增测试脚手架。

---

### Task 1: 纯片段模型与截断/区间差集

**Files:**
- Create: `frontend/src/features/editor/model.ts`
- Modify: `frontend/src/pages/EditorPage.tsx`

**Interfaces:**
- Produces: `EditorClip`、`splitClip`、`removeAtPlayhead`、`duplicateClip`、`trimClip`、`subtractWaste`、`pack`、`MIN_DUR`。

- [ ] 用当前 1 分钟片段写出浏览器复现：播放头 10 秒按截断无反应。
- [ ] 实现纯函数；`splitClip(clips,id,time)` 左段 `[trimStart,sourceCut)`、右段 `[sourceCut,trimEnd)`，两侧不足 0.3 秒返回原数组。
- [ ] `subtractWaste(clips,videoId,waste)` 只对该视频当前片段求源区间差集，其他视频和未涉及区间保持不变。
- [ ] 将页面 `pack/newClip` 移至模型模块，保持拖动/裁剪行为。
- [ ] `npm run build` 通过后，浏览器验证截断产生两段且总时长不变。

### Task 2: 工具栏、历史、快捷键和草稿

**Files:**
- Create: `frontend/src/features/editor/useEditorHistory.ts`
- Create: `frontend/src/features/editor/EditorTimeline.tsx`
- Modify: `frontend/src/pages/EditorPage.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: Task 1 的纯函数。
- Produces: `useEditorHistory(initial)` 的 `clips/title/setClips/setTitle/undo/redo/canUndo/canRedo`；`EditorTimeline` 工具栏回调。

- [ ] 浏览器先验证当前缺少撤销、复制、左右删除和草稿恢复。
- [ ] 历史仅记录有效快照；拖动结束才入栈，新操作清 redo。
- [ ] 增加截断 S、左右删除、复制、精确入点/出点、缩放和清空确认。
- [ ] 快捷键忽略 INPUT/TEXTAREA/contenteditable；Space 播放，方向键 1/5 秒。
- [ ] localStorage 保存版本化草稿，恢复时按视频 id/duration 校验，损坏数据不崩溃。
- [ ] 浏览器验证连续编辑后 undo/redo、刷新恢复和 320px 工具栏不溢出。

### Task 3: 预览组件与建议预览

**Files:**
- Create: `frontend/src/features/editor/EditorPreview.tsx`
- Modify: `frontend/src/pages/EditorPage.tsx`

**Interfaces:**
- Consumes: clips、playhead、播放回调。
- Produces: 普通时间线预览和 `previewRange(video, start, end)` 建议预览；退出恢复原播放头。

- [ ] 保留已修复的首次 seek、缓冲暂停、play rejection 错误行为。
- [ ] 将 video 事件和 RAF 循环迁入组件；截断/撤销后同步正确帧。
- [ ] 建议预览只播放指定区间，不修改 clips，结束/取消恢复之前状态。
- [ ] 浏览器验证跨片段、首次定位、缓冲和建议预览恢复。

### Task 4: 本地静音与重复候选分析

**Files:**
- Create: `backend/app/services/audio_analysis.py`
- Create: `backend/tests/test_audio_analysis.py`
- Modify: `backend/app/services/ai_edit.py`
- Modify: `backend/app/routers/edit.py`

**Interfaces:**
- Produces: `detect_silence(path, noise='-35dB', min_duration=0.6)`、`repetition_candidates(segments)`，返回统一 waste 字典。

- [ ] 先写失败测试：

```python
def test_parses_silence_intervals(self):
    lines = "silence_start: 2.5\nsilence_end: 4.0 | silence_duration: 1.5"
    self.assertEqual(parse_silence(lines), [{"start": 2.5, "end": 4.0, "kind": "silence", "reason": "静音/长停顿 1.5s", "source": "audio"}])

def test_finds_normalized_repeated_sentence(self):
    segs = [{"start": 0, "end": 1, "text": "你好，世界"}, {"start": 2, "end": 3, "text": "你好世界！"}]
    self.assertEqual(len(repetition_candidates(segs)), 1)
```

- [ ] 运行 `python -m unittest tests.test_audio_analysis -v`，确认因模块不存在失败。
- [ ] 用 FFmpeg silencedetect 实现静音；限制噪声阈值/时长并解析 stderr。
- [ ] 重复候选先本地规范化文本并配对，LLM 只判断候选；无候选不发额外请求。
- [ ] 新 kinds 兼容旧 intro/outro/ad，未知 kind 不进入结果。
- [ ] 全套 unittest 通过。

### Task 5: 长文本分块、建议 id 与兼容 API

**Files:**
- Modify: `backend/app/services/ai_edit.py`
- Modify: `backend/app/routers/edit.py`
- Modify: `backend/tests/test_audio_analysis.py`
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces: 建议 `{id,video_id,start,end,kind,reason,source}`；旧 `keep/waste` 保留。

- [ ] 先测试超长字幕按字符预算分块、上下文不重复、区间限制在当前块。
- [ ] 清洗 NaN/Infinity、反向区间、小于 0.3 秒和重叠；为每项生成稳定 id。
- [ ] 分析错误按转写/接口/解析分类，支持部分结果和错误列表。
- [ ] `AiVideoAnalysis` TS 类型扩展但旧字段仍可用。
- [ ] 后端全测与前端 TypeScript 构建通过。

### Task 6: AI 面板、选择性应用与结构收尾

**Files:**
- Create: `frontend/src/features/editor/AiAnalysisPanel.tsx`
- Modify: `frontend/src/pages/EditorPage.tsx`
- Modify: `frontend/src/index.css`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1 `subtractWaste`、Task 3 建议预览、Task 4/5 API 类型。
- Produces: 每条建议勾选、编辑、预览、全部/单独应用。

- [ ] 用模拟任务先复现旧面板只能整批应用。
- [ ] 新面板支持 silence/repeat、勾选、时间编辑、预览和失败重试。
- [ ] 应用调用 `subtractWaste` 并进入统一历史，未涉及手动片段不变。
- [ ] 更新 README 的剪辑快捷键、草稿和 AI 本地/云端边界。
- [ ] 浏览器完成“添加→截断→撤销→AI 取消一条→应用→导出”全流程；真实临时视频验证导出区间。
- [ ] 运行后端全套 unittest、compileall、前端 build、320/1024/桌面验收和 `git diff --check`。
