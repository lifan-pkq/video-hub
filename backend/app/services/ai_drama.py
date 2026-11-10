"""AI 短剧流水线：小说 → 分集分镜 → 角色锚定图 → 逐镜首帧 → 图生视频 → 合成单集。

复用现有能力：
- app.services.drama_planner：小说分析与 AI 动态分镜
- app.services.ai_media：Agnes 生图 / 生视频（多 Key 池、失败换 Key）
- app.services.profiles：人物档案（固定层提示词 + 锚定参考图，锁角色一致性）

生成产物持续质检，低分原因进入下一次提示词，直到合格或手动取消；
评分服务不可用和上游失败保留现场，可通过同一个父任务断点续跑。
"""

from __future__ import annotations

import base64
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.services import ai_media, drama_pipeline, drama_planner, drama_store, profiles
from app.services.ai_pool import AI_POOL
from app.services.media import probe_video
from app.services.tasks import task_manager

STYLES = {
    "realistic": "写实风格，电影级光影，高清细节，浅景深",
    "semi": "半写实风格，精致插画质感，柔和电影光",
    "anime": "日系动漫风格，精致赛璐璐上色，电影感构图",
    "guofeng": "国风工笔风格，东方美学，水墨意境，电影级光影",
}
THREADS: dict[str, threading.Thread] = {}
_ORCHESTRATION_REQUESTS: set[str] = set()
_LOCK = threading.RLock()


class DramaError(RuntimeError):
    """确定性错误（取消、数据缺失），不做无意义重试。"""


class _RetryableError(RuntimeError):
    """生成失败：换 Key 重试直到成功。"""


# ---------------------------------------------------------------- 项目持久化

def _dir(project_id: str) -> Path:
    try:
        drama_store.task_id_for(project_id)
    except ValueError as exc:
        raise DramaError("项目不存在") from exc
    return drama_store.DRAMA_DIR / project_id


def _assets(project_id: str) -> Path:
    path = _dir(project_id) / "assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save(project: dict[str, Any]) -> None:
    drama_store.save_project(project)


def load_project(project_id: str) -> dict[str, Any]:
    try:
        return drama_store.load_project(project_id)
    except (OSError, ValueError) as exc:
        raise DramaError("项目不存在或已损坏") from exc


def _media_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


def _summary(project: dict[str, Any]) -> dict[str, Any]:
    return {
        key: project.get(key) for key in (
            "id", "title", "status", "stage", "progress", "error",
            "created_at", "updated_at", "options", "novel_chars",
        )
    } | {
        "episodes": [
            {"index": ep.get("index"), "title": ep.get("title"), "status": ep.get("status"),
             "video": ep.get("video"), "video_url": ep.get("video_url"),
             "shots_done": sum(1 for s in ep.get("shots") or [] if _media_name(s.get("video"))),
             "shots_total": len(ep.get("shots") or [])}
            for ep in project.get("episodes") or []
        ],
    }


def list_projects() -> list[dict[str, Any]]:
    rows = []
    if not drama_store.DRAMA_DIR.is_dir():
        return rows
    for path in drama_store.DRAMA_DIR.glob("*/project.json"):
        try:
            project = load_project(path.parent.name)
        except DramaError:
            continue
        rows.append(_summary(project))
    return sorted(rows, key=lambda item: item.get("created_at") or "", reverse=True)


def asset_path(project_id: str, name: str) -> Path:
    safe = Path(name).name
    if safe != name or not re.fullmatch(r"[A-Za-z0-9_.-]+", safe):
        raise DramaError("文件不存在")
    path = _assets(project_id) / safe
    if not path.is_file():
        raise DramaError("文件不存在")
    return path


# ---------------------------------------------------------------- 工具

def _log(project: dict[str, Any], message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    project.setdefault("logs", []).append(line)
    project["logs"] = project["logs"][-500:]
    task_id = project.get("task_id")
    if task_id:
        task = task_manager.get(task_id)
        if task:
            task.log(message)


def _check_cancel(project: dict[str, Any]) -> None:
    if project.get("cancel_requested"):
        raise DramaError("项目已取消")
    if drama_store.load_project(project["id"]).get("cancel_requested"):
        project["cancel_requested"] = True
        raise DramaError("项目已取消")


def _retry(project: dict[str, Any], label: str, fn, attempts: int | None = None):
    """失败自动换 Key 重试直到成功（上限 max_attempts，防全 Key 失效死循环）。"""
    limit = attempts or int(project["options"].get("max_attempts") or 8)
    last: Exception | None = None
    for attempt in range(1, limit + 1):
        _check_cancel(project)
        try:
            return fn()
        except DramaError:
            raise
        except Exception as exc:  # noqa: BLE001 - 上游错误类型多，统一换 Key 重试
            last = exc
            _log(project, f"{label} 第 {attempt}/{limit} 次失败：{str(exc)[:160]}")
            if attempt < limit:
                _log(project, f"{label} 自动换 Key，10 秒后重试…")
                time.sleep(10)
            _save(project)
    raise DramaError(f"{label} 连续 {limit} 次失败：{str(last)[:200]}。可检查 Key 池后点「继续」断点续跑。")


def _llm_json(project: dict[str, Any], label: str, system: str, user: str) -> dict[str, Any]:
    def call() -> dict[str, Any]:
        from app.services.understanding import _request_json

        return _request_json([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])

    return _retry(project, label, call)


def _data_uri(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else f"image/{suffix.removeprefix('.')}"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _to_jpeg(source: Path, target: Path) -> Path:
    """生成图统一转 JPEG：锚定图/首帧都要反复当参考图上传，JPEG 体积只有 PNG 零头。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-q:v", "3", str(target)],
        capture_output=True, timeout=120, check=False,
    )
    if result.returncode != 0 or not target.is_file():
        shutil.copyfile(source, target)
    return target


def _shot_characters(project: dict[str, Any], shot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for cid in shot.get("characters") or []:
        for character in project.get("characters") or []:
            if character.get("id") == cid:
                rows.append(character)
    if not rows and project.get("characters"):
        rows = [project["characters"][0]]
    return rows


def _cast_description(cast: list[dict[str, Any]]) -> str:
    return "；".join(
        f"{c['name']}（{c.get('appearance')}，{c.get('clothing')}）" for c in cast
    )


# ---------------------------------------------------------------- 步骤一：小说分析

ANALYZE_SYSTEM = drama_planner.ANALYZE_SYSTEM


def _analyze(project: dict[str, Any]) -> None:
    if project.get("characters") and project.get("episodes"):
        return
    novel = (_dir(project["id"]) / "novel.txt").read_text(encoding="utf-8")
    # 集数由 AI 根据小说篇幅和剧情节奏自主决定，不再用表单里的集数。
    result = _retry(
        project, "分析小说", lambda: drama_planner.plan_novel(novel, None)
    )
    project["title"] = project["title"] or str(result.get("title") or "AI 短剧")[:60]
    project["logline"] = str(result.get("logline") or "")
    characters = []
    for index, item in enumerate(result.get("characters") or []):
        characters.append({
            "id": f"c{index + 1}",
            "name": str(item.get("name") or f"角色{index + 1}")[:30],
            "age": item.get("age"),
            "gender": str(item.get("gender") or ""),
            "appearance": str(item.get("appearance") or ""),
            "clothing": str(item.get("clothing") or ""),
            "personality": str(item.get("personality") or ""),
            "profile_id": "",
            "anchor": "",
            "anchor_url": "",
            "status": "pending",
        })
    if not characters:
        raise DramaError("小说分析没有提取到角色，请补充更完整的小说内容")
    raw_episodes = result["episodes"]
    episodes = []
    for item in raw_episodes:
        index = int(item["index"])
        episodes.append({
            "index": index,
            "title": str(item["title"])[:60],
            "summary": str(item.get("summary") or ""),
            "source_excerpt": str(item.get("source_excerpt") or ""),
            "shots": [],
            "video": "",
            "srt": "",
            "status": "pending",
        })
    project["characters"] = characters
    project["episodes"] = episodes
    # 把 AI 定下来的集数写回 options，列表页显示的是实际集数。
    project["options"]["episodes_count"] = len(episodes)
    _save(project)


# ---------------------------------------------------------------- 步骤二：角色定妆（锚定图 + 档案）

def _character_payload(project: dict[str, Any], character: dict[str, Any], anchor: str = "") -> dict[str, Any]:
    return {
        "id": character["profile_id"],
        "name": f"{project['title']}·{character['name']}",
        "fixed_features": character.get("appearance") or "",
        "clothing": character.get("clothing") or "",
        "age_rules": f"年龄固定为 {character.get('age') or '成年'}，全剧不变",
        "references": [{"name": anchor}] if anchor else [],
    }


def _build_characters(project: dict[str, Any]) -> None:
    ratio = project["options"]["aspect_ratio"]
    threshold = int(project["options"].get("quality_threshold") or 70)
    for character in project.get("characters") or []:
        if character.get("profile_id") and character.get("anchor"):
            continue
        _check_cancel(project)
        name = character["name"]
        if not character.get("profile_id"):
            character["profile_id"] = f"drama-{project['id']}-{character['id']}"
            profiles.save_profile("characters", _character_payload(project, character))
            _save(project)
        profile_id = character["profile_id"]
        prompt = (
            f"角色定妆照：{name}，{character.get('age') or ''} 岁，{character.get('gender')}。"
            f"{character.get('appearance')}。{character.get('clothing')}。"
            f"气质：{character.get('personality')}。"
            "正面半身像，面部特征清晰无遮挡，光线均匀，纯色简洁背景，"
            "这是一张用于全剧角色一致性锁定的锚定图。"
        )

        def cancelled() -> bool:
            return bool(
                project.get("cancel_requested")
                or drama_store.project_cancelled(project["id"])
            )

        def record(quality, attempt: int, effective_prompt: str) -> None:
            character.setdefault("quality_history", []).append({
                "attempt": attempt,
                "score": quality.score,
                "passed": quality.passed,
                "reasons": list(quality.reasons),
                "effective_prompt": effective_prompt,
            })
            _save(project)

        media, quality = drama_pipeline.run_quality_loop(
            prompt=prompt,
            generate=lambda effective_prompt: drama_pipeline.media_generation.generate_image_asset(
                effective_prompt,
                size="2K",
                ratio=ratio,
                should_cancel=cancelled,
            ),
            score=lambda item: drama_pipeline.media_quality.score_image(
                item.path, prompt, [], threshold
            ),
            threshold=threshold,
            should_cancel=cancelled,
            record=record,
        )
        jpeg = _to_jpeg(
            media.path,
            _assets(project["id"]) / f"anchor_{character['id']}.jpg",
        )
        reference = profiles.save_reference(jpeg.read_bytes(), jpeg.name)
        if media.path != jpeg:
            media.path.unlink(missing_ok=True)
        profiles.save_profile(
            "characters", _character_payload(project, character, reference["name"]), profile_id=profile_id,
        )
        character["anchor"] = reference["name"]
        character["anchor_url"] = f"/api/profiles/references/{reference['name']}"
        character["quality"] = quality.as_dict()
        character["status"] = "done"
        _log(project, f"角色「{name}」锚定图已生成、质检并写入人物档案")
        _save(project)


# ---------------------------------------------------------------- 步骤三：分集分镜

STORYBOARD_SYSTEM = drama_planner.STORYBOARD_SYSTEM


def _storyboard(project: dict[str, Any]) -> None:
    options = project["options"]
    style = STYLES.get(options.get("style") or "realistic", STYLES["realistic"])
    for episode in project.get("episodes") or []:
        if episode.get("shots"):
            continue
        _check_cancel(project)
        character_brief = [
            {"id": c["id"], "name": c["name"], "appearance": c["appearance"], "clothing": c["clothing"]}
            for c in project["characters"]
        ]
        shots = _retry(
            project,
            f"分镜·第 {episode['index']} 集",
            lambda episode=episode, character_brief=character_brief: drama_planner.plan_episode(
                episode, character_brief, title=project["title"], style=style
            ),
        )
        episode["shots"] = shots
        episode["status"] = "storyboard"
        _save(project)


# ---------------------------------------------------------------- 步骤四：逐镜首帧图

def _frame_prompt(project: dict[str, Any], episode: dict[str, Any], shot: dict[str, Any]) -> str:
    style = STYLES.get(project["options"].get("style") or "realistic", STYLES["realistic"])
    cast = _shot_characters(project, shot)
    return (
        f"短剧《{project['title']}》第 {episode['index']} 集第 {shot['index']} 镜首帧。"
        f"画风：{style}。场景：{shot['scene']}。"
        f"出镜角色：{_cast_description(cast) or '按人物档案'}。"
        "严格保持人物档案与参考图的固定面部特征、发型与服装，禁止换脸。"
        f"画面定格在动作开始瞬间：{shot['action']}。表情：{shot['expression']}。"
        "电影感构图，主体清晰，为后续图生视频的首帧。"
    )


def _build_frames(project: dict[str, Any]) -> None:
    ratio = project["options"]["aspect_ratio"]
    for episode in project.get("episodes") or []:
        for shot in episode.get("shots") or []:
            if _media_name(shot.get("image")):
                continue
            _check_cancel(project)
            cast = _shot_characters(project, shot)
            main = cast[0] if cast else {}
            extra_refs: list[str] = []
            for character in cast[1:]:
                try:
                    profile = profiles.get_profile("characters", character["profile_id"], include_data=True)
                    extra_refs.extend(
                        item["data_uri"] for item in profile.get("references") or [] if item.get("data_uri")
                    )
                except Exception:  # noqa: BLE001
                    continue
            label = f"首帧·E{episode['index']}S{shot['index']}"

            def draw() -> dict[str, Any]:
                return ai_media.generate_image(
                    prompt=_frame_prompt(project, episode, shot),
                    size="2K",
                    ratio=ratio,
                    images=extra_refs[:2],
                    character_profile_id=main.get("profile_id") or "",
                )

            result = _retry(project, label, draw)
            frame_name = f"e{episode['index']}s{shot['index']}.jpg"
            _to_jpeg(ai_media.GENERATED_IMAGE_DIR / result["name"], _assets(project["id"]) / frame_name)
            image = shot["image"]
            image["name"] = frame_name
            image["url"] = f"/api/drama/projects/{project['id']}/files/{frame_name}"
            image["status"] = "ready"
            image["revision"] = shot["revision"]
            image["attempts"] = int(image.get("attempts") or 0) + 1
            shot["frame_url"] = image["url"]
            shot["status"] = "framed"
            shot["attempts"] = int(shot.get("attempts") or 0) + 1
            _save(project)
        episode["status"] = "framed"
        _save(project)


# ---------------------------------------------------------------- 步骤五：逐镜视频（失败换 Key 直到成功）

def _video_prompt(project: dict[str, Any], shot: dict[str, Any]) -> str:
    action_prompt = shot.get("prompt") or (
        f"{shot.get('action', '')}。运镜：{shot.get('camera', '固定')}。"
        f"表情变化：{shot.get('expression', '')}"
    )
    prompt = (
        f"从首帧开始，严格遵循镜头提示词：{action_prompt}。"
        "运镜缓慢稳定。人物外貌、服装、场景光线与首帧完全一致，不变脸、不换人、不切换场景，"
        "动作缓慢自然，无快速剧烈运动。"
    )
    if shot.get("dialogue"):
        prompt += f"角色说台词时口型自然：「{shot['dialogue']}」。"
    return prompt


def _build_videos(project: dict[str, Any]) -> None:
    ratio = project["options"]["aspect_ratio"]
    for episode in project.get("episodes") or []:
        for shot in episode.get("shots") or []:
            if _media_name(shot.get("video")):
                continue
            frame_name = _media_name(shot.get("image"))
            if not frame_name:
                raise DramaError(f"E{episode['index']}S{shot['index']} 缺少首帧，无法生成视频")
            _check_cancel(project)
            frame_path = _assets(project["id"]) / frame_name
            if not frame_path.is_file():
                shot["image"].update({"name": "", "url": "", "status": "pending"})
                shot["frame_url"] = ""
                _save(project)
                raise DramaError(f"E{episode['index']}S{shot['index']} 首帧文件丢失，请点「继续」重新生成")
            label = f"视频·E{episode['index']}S{shot['index']}"

            def make() -> str:
                generated = ai_media.generate_video_asset(
                    prompt=_video_prompt(project, shot),
                    seconds=int(shot["seconds"]),
                    aspect_ratio=ratio,
                    first_frame=_data_uri(frame_path),
                )
                target = _assets(project["id"]) / f"e{episode['index']}s{shot['index']}.mp4"
                shutil.copyfile(generated.path, target)
                return target.name

            video_name = _retry(project, label, make)
            video = shot["video"]
            video["name"] = video_name
            video["url"] = f"/api/drama/projects/{project['id']}/files/{video_name}"
            video["status"] = "ready"
            video["revision"] = shot["revision"]
            video["attempts"] = int(video.get("attempts") or 0) + 1
            shot["video_url"] = video["url"]
            shot["status"] = "done"
            shot["attempts"] = int(shot.get("attempts") or 0) + 1
            _log(project, f"{label} 完成")
            _save(project)
        episode["status"] = "videos"
        _save(project)


# ---------------------------------------------------------------- 步骤六：合成单集 + 台词字幕

def _srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _episode_output_current(project_id: str, episode: dict[str, Any]) -> bool:
    video_name = _media_name(episode.get("video"))
    if episode.get("status") != "done" or not video_name:
        return False
    if not (_assets(project_id) / video_name).is_file():
        return False
    expected = {
        str(shot.get("id")): int(shot.get("revision") or 1)
        for shot in episode.get("shots") or []
    }
    composed = episode.get("composition_revisions")
    return composed is None or composed == expected


def _compose(project: dict[str, Any]) -> None:
    project_id = project["id"]
    episode_indexes = [int(episode["index"]) for episode in project.get("episodes") or []]
    for episode_index in episode_indexes:
        current = load_project(project_id)
        _check_cancel(current)
        episode = next(
            item for item in current.get("episodes") or [] if int(item["index"]) == episode_index
        )
        if _episode_output_current(project_id, episode):
            continue
        shots = episode.get("shots") or []
        expected_revisions = {
            str(shot.get("id")): int(shot.get("revision") or 1) for shot in shots
        }
        if not shots or not all(
            drama_pipeline.shot_video_eligible(shot, int(shot.get("revision") or 1))
            for shot in shots
        ):
            raise DramaError(f"第 {episode_index} 集存在未通过质量门或已过期的镜头视频")
        lines = []
        srt_blocks = []
        cursor = 0.0
        assets = _assets(project_id)
        for index, shot in enumerate(shots):
            clip = assets / _media_name(shot.get("video"))
            if not clip.is_file():
                raise DramaError(f"第 {episode_index} 集镜头 {shot['index']} 视频缺失")
            lines.append(f"file '{clip.name}'")
            duration = float(probe_video(clip).get("duration") or shot["seconds"])
            if shot.get("dialogue"):
                srt_blocks.append(
                    f"{index + 1}\n{_srt_time(cursor)} --> {_srt_time(cursor + duration)}\n{shot['dialogue']}\n"
                )
            cursor += duration
        token = uuid.uuid4().hex
        concat = assets / f".e{episode_index}_{token}_concat.txt"
        output = assets / f".episode_{episode_index}_{token}.tmp.mp4"
        srt = assets / f".episode_{episode_index}_{token}.tmp.srt"
        burned = assets / f".episode_{episode_index}_{token}_subtitled.tmp.mp4"
        video_name = f"episode_{episode_index}.mp4"
        srt_name = f"episode_{episode_index}.srt"
        try:
            concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
            srt.write_text("\n".join(srt_blocks), encoding="utf-8")

            def run(copy: bool) -> None:
                command = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat)]
                if copy:
                    command += ["-c", "copy"]
                else:
                    command += ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an"]
                command.append(str(output))
                result = subprocess.run(command, capture_output=True, timeout=1800, check=False)
                if result.returncode != 0 or not output.is_file():
                    raise DramaError(f"ffmpeg 合成失败：{result.stderr.decode('utf-8', 'ignore')[:200]}")

            try:
                run(copy=True)
            except DramaError:
                run(copy=False)
            if current["options"].get("burn_subtitles") and srt_blocks:
                result = subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(output),
                     "-vf", f"subtitles={srt.name}", "-c:a", "copy", str(burned)],
                    capture_output=True, timeout=1800, cwd=str(assets), check=False,
                )
                if result.returncode == 0 and burned.is_file():
                    burned.replace(output)
                else:
                    def log_subtitle_failure(latest: dict[str, Any]) -> None:
                        _log(latest, f"第 {episode_index} 集字幕烧录失败，保留无字幕成片与 SRT 文件")

                    drama_store.update_project(project_id, log_subtitle_failure)
            published = drama_store.publish_episode(
                project_id,
                episode_index,
                expected_revisions,
                temp_video=output,
                temp_srt=srt,
                video_name=video_name,
                srt_name=srt_name,
                duration=cursor,
            )
            if not published:
                raise drama_pipeline.WaitingReview(f"第 {episode_index} 集合成期间镜头已修改，旧合成结果已丢弃")

            def log_composed(latest: dict[str, Any]) -> None:
                _log(latest, f"第 {episode_index} 集合成完成（{cursor:.0f} 秒）")

            drama_store.update_project(project_id, log_composed)
        finally:
            for temporary in (concat, output, srt, burned):
                temporary.unlink(missing_ok=True)


# ---------------------------------------------------------------- 流水线主控

def _set_stage(project: dict[str, Any], stage: str, progress: float) -> None:
    project["stage"] = stage
    project["progress"] = round(progress, 1)
    task_id = project.get("task_id")
    if task_id:
        task = task_manager.get(task_id)
        if task:
            task.progress = progress
    _save(project)


def _shot_ratio(project: dict[str, Any], key: str) -> float:
    shots = [s for ep in project["episodes"] for s in ep.get("shots") or []]
    if not shots:
        return 0.0
    eligible = drama_pipeline.shot_image_eligible if key == "image" else drama_pipeline.shot_video_eligible
    return sum(
        1 for shot in shots if eligible(shot, int(shot.get("revision") or 1))
    ) / len(shots)


def _finish_project(project_id: str) -> None:
    def finish(current: dict[str, Any]) -> None:
        if any(
            not _episode_output_current(project_id, episode)
            for episode in current.get("episodes") or []
        ):
            raise drama_pipeline.WaitingReview("合成后镜头或剧集已修改，等待重新生成")
        current["status"] = "success"
        current["stage"] = "全部完成"
        current["progress"] = 100.0
        current["error"] = ""
        current["recoverable"] = False
        task = task_manager.get(current["task_id"])
        if task:
            task.progress = 100.0
        _log(current, "短剧全部集数生成完成")

    drama_store.update_project(project_id, finish)


def _run(project_id: str) -> None:
    project = load_project(project_id)
    try:
        project["status"] = "running"
        _set_stage(project, "分析小说：提取角色与分集大纲", 2)
        _analyze(project)
        _set_stage(project, "角色定妆：生成锚定图与人物档案", 8)
        _build_characters(project)
        _set_stage(project, "分集分镜：生成逐镜剧本", 18)
        _storyboard(project)
        _set_stage(project, "逐镜生成首帧图与质量检查", 20 + 35 * _shot_ratio(project, "image"))
        drama_pipeline._run_image_stage(project_id)
        project = load_project(project_id)
        _set_stage(project, "全部镜头并发生成视频与质量检查", 55 + 35 * _shot_ratio(project, "video"))
        drama_pipeline._run_video_stage(project_id)
        project = load_project(project_id)
        _set_stage(project, "合成单集与台词字幕", 92)
        _compose(project)
        _finish_project(project_id)
    except drama_pipeline.WaitingReview as exc:
        def mark_waiting(current: dict[str, Any]) -> None:
            current["status"] = "waiting_review"
            current["error"] = str(exc)[:400]
            _log(current, "有镜头等待修改或重试，其他镜头产物已保留")

        drama_store.update_project(project_id, mark_waiting)
    except Exception as exc:  # noqa: BLE001
        cancelled = "已取消" in str(exc) or drama_store.project_cancelled(project_id)

        def mark_interrupted(current: dict[str, Any]) -> None:
            current["status"] = "cancelled" if cancelled else "failed"
            current["error"] = "" if cancelled else str(exc)[:400]
            current["recoverable"] = not cancelled
            _log(current, ("项目已取消" if cancelled else f"项目中断：{str(exc)[:200]}") + "，可点「继续」断点续跑")

        drama_store.update_project(project_id, mark_interrupted)
    finally:
        rerun: threading.Thread | None = None
        with _LOCK:
            THREADS.pop(project_id, None)
            if project_id in _ORCHESTRATION_REQUESTS:
                _ORCHESTRATION_REQUESTS.discard(project_id)
                rerun = threading.Thread(target=_run, args=(project_id,), daemon=True)
                THREADS[project_id] = rerun
        if rerun:
            rerun.start()


def _spawn(project: dict[str, Any], target=None) -> None:
    with _LOCK:
        thread = THREADS.get(project["id"])
        if thread and thread.is_alive():
            return
        project["task_id"] = drama_store.task_id_for(project["id"])
        thread = threading.Thread(target=target or _run, args=(project["id"],), daemon=True)
        THREADS[project["id"]] = thread
        thread.start()


def _plan(project_id: str) -> None:
    """第一段：只做规划——分析小说、分集分镜，不生成任何图片/视频。
    规划完把项目置为 planned，等用户在结果页点「开始生成」再跑 _run。
    _run 里 _analyze/_storyboard 都有“已有数据就跳过”的保护，不会重做规划。"""
    project = load_project(project_id)
    try:
        project["status"] = "running"
        _set_stage(project, "分析小说：AI 决定集数与角色", 2)
        _analyze(project)
        _set_stage(project, "分集分镜：AI 决定每集镜头与时长", 10)
        _storyboard(project)
        project = load_project(project_id)
        total_shots = sum(len(ep.get("shots") or []) for ep in project.get("episodes") or [])

        def mark_planned(current: dict[str, Any]) -> None:
            current["status"] = "planned"
            current["stage"] = "分镜规划完成，等待开始生成"
            current["progress"] = 18.0
            current["planned"] = True
            current["error"] = ""
            _log(current, f"分镜规划完成：{len(current.get('episodes') or [])} 集、{total_shots} 个镜头，等待开始生成")

        drama_store.update_project(project_id, mark_planned)
    except Exception as exc:  # noqa: BLE001
        cancelled = "已取消" in str(exc) or drama_store.project_cancelled(project_id)

        def mark_failed(current: dict[str, Any]) -> None:
            current["status"] = "cancelled" if cancelled else "failed"
            current["error"] = "" if cancelled else str(exc)[:400]
            current["recoverable"] = not cancelled
            _log(current, ("项目已取消" if cancelled else f"规划中断：{str(exc)[:200]}") + "，可删除后重新创建")

        drama_store.update_project(project_id, mark_failed)
    finally:
        rerun: threading.Thread | None = None
        with _LOCK:
            THREADS.pop(project_id, None)
            if project_id in _ORCHESTRATION_REQUESTS:
                _ORCHESTRATION_REQUESTS.discard(project_id)
                rerun = threading.Thread(target=_run, args=(project_id,), daemon=True)
                THREADS[project_id] = rerun
        if rerun:
            rerun.start()


def plan_project(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    _spawn(project, target=_plan)
    return _summary(project)


def request_orchestration(project_id: str) -> None:
    with _LOCK:
        thread = THREADS.get(project_id)
        if thread and thread.is_alive():
            _ORCHESTRATION_REQUESTS.add(project_id)
            return
        _ORCHESTRATION_REQUESTS.discard(project_id)
        _spawn(load_project(project_id))


def start_project(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    _spawn(project)
    return _summary(project)


def create_project(novel: str, title: str, options: dict[str, Any]) -> dict[str, Any]:
    if not AI_POOL.has_keys():
        raise DramaError("没有配置 AI API Key")
    text = (novel or "").strip()
    if len(text) < 200:
        raise DramaError("小说内容太短（至少 200 字），请提供完整小说内容")
    if len(text) > 300000:
        raise DramaError("小说内容过长（上限 30 万字）")
    project_id = uuid.uuid4().hex[:12]
    project = {
        "id": project_id,
        "title": (title or "").strip()[:60],
        "logline": "",
        "status": "pending",
        "stage": "排队中",
        "progress": 0.0,
        "error": "",
        "novel_chars": len(text),
        "options": options,
        "characters": [],
        "episodes": [],
        "logs": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "task_id": drama_store.task_id_for(project_id),
        "cancel_requested": False,
    }
    folder = _dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "novel.txt").write_text(text, encoding="utf-8")
    _assets(project_id)
    _save(project)
    # 创建后只跑规划（分析+分镜），不生成图片/视频，等用户在结果页确认后点「开始生成」。
    return plan_project(project_id)


def start_generation(project_id: str) -> dict[str, Any]:
    """第二段：用户在结果页确认分镜规划后点「开始生成」，从角色定妆开始正式生成。"""
    project = load_project(project_id)
    with _LOCK:
        thread = THREADS.get(project_id)
        if thread and thread.is_alive():
            raise DramaError("项目正在运行中")
    if project.get("status") not in {"planned", "pending"}:
        raise DramaError("项目不在可开始状态")
    if not project.get("episodes") or not any(ep.get("shots") for ep in project["episodes"]):
        raise DramaError("分镜规划尚未完成，请稍候再开始")
    return start_project(project_id)


def resume_project(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    with _LOCK:
        thread = THREADS.get(project_id)
        if thread and thread.is_alive():
            raise DramaError("项目正在运行中")
    if project.get("status") == "success":
        raise DramaError("项目已完成")

    def prepare_resume(current: dict[str, Any]) -> None:
        current["cancel_requested"] = False
        current["error"] = ""

    drama_store.update_project(project_id, prepare_resume)
    return start_project(project_id)


def generate_storyboard(project_id: str) -> dict[str, Any]:
    try:
        return drama_pipeline.generate_storyboard(project_id)
    except (ValueError, drama_pipeline.DramaPipelineError) as exc:
        raise DramaError(str(exc)) from exc


def cancel_project(project_id: str) -> dict[str, Any]:
    project = drama_pipeline.cancel_project(project_id)
    return _summary(project)


def update_shot(
    project_id: str,
    shot_id: str,
    *,
    prompt: str | None = None,
    seconds: int | None = None,
    reference_images: list[str] | None = None,
) -> dict[str, Any]:
    try:
        return drama_pipeline.update_shot(
            project_id,
            shot_id,
            prompt=prompt,
            seconds=seconds,
            reference_images=reference_images,
        )
    except (ValueError, drama_pipeline.DramaPipelineError) as exc:
        raise DramaError(str(exc)) from exc


def retry_shot(project_id: str, shot_id: str, stage: str = "all") -> dict[str, Any]:
    if stage == "video":
        project = load_project(project_id)
        shot = next((
            shot
            for episode in project.get("episodes") or []
            for shot in episode.get("shots") or []
            if shot.get("id") == shot_id
        ), None)
        revision = int((shot or {}).get("revision") or 1)
        if shot is None:
            raise DramaError("镜头不存在")
        eligibility_shot = shot | {"status": "pending"} if shot.get("status") == "cancelled" else shot
        if not drama_pipeline.shot_image_eligible(eligibility_shot, revision):
            raise DramaError(f"镜头 {shot_id} 首帧尚未通过质量门")
    try:
        drama_pipeline.retry_shot(project_id, shot_id, stage)
    except (ValueError, drama_pipeline.DramaPipelineError) as exc:
        raise DramaError(str(exc)) from exc
    return {"id": shot_id, "status": "running", "stage": stage}


def cancel_shot(project_id: str, shot_id: str) -> dict[str, Any]:
    try:
        project = drama_pipeline.cancel_shot(project_id, shot_id)
    except (ValueError, drama_pipeline.DramaPipelineError) as exc:
        raise DramaError(str(exc)) from exc
    for episode in project.get("episodes") or []:
        for shot in episode.get("shots") or []:
            if shot.get("id") == shot_id:
                return shot
    raise DramaError("镜头不存在")


def delete_project(project_id: str) -> None:
    with _LOCK:
        thread = THREADS.get(project_id)
        if thread and thread.is_alive():
            raise DramaError("项目正在运行，请先取消")
    folder = _dir(project_id)
    if folder.is_dir():
        shutil.rmtree(folder)


def project_detail(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    project.pop("cancel_requested", None)
    return project
