from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.services import ai_images, ai_media, drama_store, media_generation, media_quality, profiles
from app.services.media_generation import (
    CancelCheck,
    GeneratedMedia,
    GenerationCancelled,
    MediaAIError,
)
from app.services.media_quality import QualityResult, QualityUnavailable


class DramaPipelineError(RuntimeError):
    status = "failed"


class WaitingReview(DramaPipelineError):
    status = "waiting_review"


@dataclass
class ShotRuntime:
    cancel: threading.Event
    thread: threading.Thread | None = None


_SHOT_RUNTIMES: dict[tuple[str, str], ShotRuntime] = {}
_SHOT_RUNTIMES_LOCK = threading.RLock()


def _find_shot(project: dict[str, Any], shot_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for episode in project.get("episodes") or []:
        for shot in episode.get("shots") or []:
            if shot.get("id") == shot_id:
                return episode, shot
    raise DramaPipelineError("镜头不存在")


def _artifact_eligible(shot: dict[str, Any], stage: str, revision: int) -> bool:
    if shot.get("status") == "cancelled":
        return False
    artifact = shot.get(stage) or {}
    if artifact.get("status") != "ready":
        return False
    if int(artifact.get("revision") or 0) != int(revision):
        return False
    quality = artifact.get("quality")
    if isinstance(quality, dict):
        return quality.get("passed") is True
    return quality is None and artifact.get("legacy_accepted") is True


def shot_video_eligible(shot: dict[str, Any], revision: int) -> bool:
    return _artifact_eligible(shot, "video", revision)


def shot_image_eligible(shot: dict[str, Any], revision: int) -> bool:
    return _artifact_eligible(shot, "image", revision)


def _wait_before_retry(attempt: int, should_cancel: CancelCheck) -> None:
    remaining = min(30.0, float(2 ** min(attempt - 1, 5)))
    while remaining > 0:
        if should_cancel():
            raise GenerationCancelled("生成已取消")
        interval = min(0.2, remaining)
        time.sleep(interval)
        remaining -= interval


def _generate_with_retry(
    generate: Callable[[str], GeneratedMedia], prompt: str, should_cancel: CancelCheck
) -> GeneratedMedia:
    attempt = 0
    while True:
        if should_cancel():
            raise GenerationCancelled("生成已取消")
        try:
            return generate(prompt)
        except GenerationCancelled:
            raise
        except media_generation.TransientMediaAIError:
            attempt += 1
            _wait_before_retry(attempt, should_cancel)


# 评分（理解模型）503 队列满是服务方短暂拥堵，不是图有问题：同一张图换 Key 也可能全撞满，
# 所以评分失败要等一会儿再试，而不是一次就把镜头和整个项目打停。每次重试都会重新走 Key 池。
_SCORE_RETRY_DELAYS = (10.0, 20.0, 30.0, 60.0)


def _score_with_retry(
    score: Callable[[GeneratedMedia], QualityResult],
    media: GeneratedMedia,
    should_cancel: CancelCheck,
) -> QualityResult:
    attempt = 0
    while True:
        try:
            return score(media)
        except QualityUnavailable:
            if attempt >= len(_SCORE_RETRY_DELAYS):
                raise
            remaining = _SCORE_RETRY_DELAYS[attempt]
            attempt += 1
            while remaining > 0:
                if should_cancel():
                    raise GenerationCancelled("生成已取消")
                interval = min(0.2, remaining)
                time.sleep(interval)
                remaining -= interval


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
    previous_rejected: GeneratedMedia | None = None
    while not should_cancel():
        attempt += 1
        effective_prompt = prompt
        if reasons:
            effective_prompt += "\n上一版需要修正：" + "；".join(reasons)
        media = _generate_with_retry(generate, effective_prompt, should_cancel)
        if previous_rejected is not None:
            previous_rejected.path.unlink(missing_ok=True)
        if should_cancel():
            media.path.unlink(missing_ok=True)
            raise GenerationCancelled("生成已取消")
        quality = _score_with_retry(score, media, should_cancel)
        if should_cancel():
            media.path.unlink(missing_ok=True)
            raise GenerationCancelled("生成已取消")
        record(quality, attempt, effective_prompt)
        if quality.passed and quality.score >= threshold:
            if previous_rejected is not None and previous_rejected.path != media.path:
                previous_rejected.path.unlink(missing_ok=True)
            return media, quality
        previous_rejected = media
        reasons = quality.reasons or [f"质量分 {quality.score} 低于 {threshold}"]
    raise GenerationCancelled("生成已取消")


def _register_shot_runtime(project_id: str, shot_id: str) -> ShotRuntime:
    key = (project_id, shot_id)
    runtime = ShotRuntime(cancel=threading.Event(), thread=threading.current_thread())
    with _SHOT_RUNTIMES_LOCK:
        current = _SHOT_RUNTIMES.get(key)
        if (
            current
            and current.thread
            and current.thread.is_alive()
            and not current.cancel.is_set()
        ):
            raise DramaPipelineError(f"镜头 {shot_id} 正在运行")
        _SHOT_RUNTIMES[key] = runtime
    return runtime


def _unregister_shot_runtime(project_id: str, shot_id: str, runtime: ShotRuntime) -> None:
    with _SHOT_RUNTIMES_LOCK:
        key = (project_id, shot_id)
        if _SHOT_RUNTIMES.get(key) is runtime:
            _SHOT_RUNTIMES.pop(key, None)


def _cancel_all_shots(project_id: str) -> None:
    with _SHOT_RUNTIMES_LOCK:
        runtimes = [
            runtime for (current_project, _), runtime in _SHOT_RUNTIMES.items()
            if current_project == project_id
        ]
    for runtime in runtimes:
        runtime.cancel.set()


def cancel_project(project_id: str) -> dict[str, Any]:
    project = drama_store.mark_project_cancelled(project_id)
    _cancel_all_shots(project_id)
    return project


def _stop_shot_runtime(project_id: str, shot_id: str) -> None:
    with _SHOT_RUNTIMES_LOCK:
        runtime = _SHOT_RUNTIMES.get((project_id, shot_id))
    if runtime:
        runtime.cancel.set()


def update_shot(
    project_id: str,
    shot_id: str,
    *,
    prompt: str | None = None,
    seconds: int | None = None,
    reference_images: list[str] | None = None,
) -> dict[str, Any]:
    if prompt is None and seconds is None and reference_images is None:
        raise ValueError("没有需要修改的镜头字段")
    if seconds is not None and not 4 <= seconds <= 12:
        raise ValueError("镜头时长必须为 4 到 12 秒")
    if reference_images is not None:
        for name in reference_images:
            drama_store.validate_reference_name(name)
    _stop_shot_runtime(project_id, shot_id)
    return drama_store.update_shot(
        project_id,
        shot_id,
        prompt=prompt,
        seconds=seconds,
        reference_images=reference_images,
    )


def cancel_shot(project_id: str, shot_id: str) -> dict[str, Any]:
    project = drama_store.mark_shot_cancelled(project_id, shot_id)
    _stop_shot_runtime(project_id, shot_id)
    return project


def _record_quality(
    project_id: str,
    shot_id: str,
    stage: str,
    revision: int,
    quality: QualityResult,
    attempt: int,
    effective_prompt: str,
) -> None:
    def update(project: dict[str, Any]) -> None:
        _, shot = _find_shot(project, shot_id)
        if int(shot.get("revision") or 1) != revision:
            return
        shot.setdefault("quality_history", []).append({
            "stage": stage,
            "revision": revision,
            "attempt": attempt,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "score": quality.score,
            "passed": quality.passed,
            "reasons": list(quality.reasons),
            "checker": quality.checker,
            "effective_prompt": effective_prompt,
            "error": "",
        })
        shot[stage]["attempts"] = attempt

    drama_store.update_project(project_id, update)


def _image_references(project: dict[str, Any], shot: dict[str, Any]) -> list[str]:
    from app.services import ai_drama

    references = []
    for item in shot.get("reference_images") or []:
        try:
            name = drama_store.validate_reference_name(item)
        except ValueError as exc:
            raise DramaPipelineError(str(exc)) from exc
        directory = ai_media.GENERATED_REFERENCE_DIR if name.startswith("ref_") else ai_images.AI_IMAGES_DIR
        path = directory / name
        if not path.is_file():
            raise DramaPipelineError(f"参考图 {name} 不存在")
        references.append(ai_drama._data_uri(path))
    character_ids = set(shot.get("characters") or [])
    for character in project.get("characters") or []:
        if character_ids and character.get("id") not in character_ids:
            continue
        profile_id = character.get("profile_id")
        if not profile_id:
            continue
        try:
            profile = profiles.get_profile("characters", profile_id, include_data=True)
        except (OSError, ValueError):
            continue
        references.extend(
            str(item["data_uri"])
            for item in profile.get("references") or []
            if item.get("data_uri")
        )
    return references[:5]


def _image_prompt(project: dict[str, Any], shot: dict[str, Any]) -> str:
    original = shot.get("prompt") or (
        f"场景：{shot.get('scene', '')}。动作：{shot.get('action', '')}。"
        f"表情：{shot.get('expression', '')}。运镜：{shot.get('camera', '')}。"
    )
    return (
        f"漫剧《{project.get('title') or project['id']}》镜头首帧。{original}\n"
        "人物与参考图保持一致，画面清晰，构图完整，严格符合场景、动作和表情。"
    )


def _video_prompt(shot: dict[str, Any]) -> str:
    action_prompt = shot.get("prompt") or (
        f"{shot.get('action', '')}。运镜：{shot.get('camera', '固定')}。"
        f"表情变化：{shot.get('expression', '')}"
    )
    prompt = (
        f"从首帧开始，严格遵循镜头提示词：{action_prompt}。"
        "运镜缓慢稳定。人物外貌、服装、场景光线与首帧完全一致，不变脸、不换人、不切换场景。"
    )
    if shot.get("dialogue"):
        prompt += f"角色说台词时口型自然：「{shot['dialogue']}」。"
    return prompt


# ---------------------------------------------------------------- 连环画分镜图
# 每镜除首帧外，再按剧情节拍出若干张分镜图：第 1 张就是首帧图，
# 第 2 张起依次是动作进行、高潮、收尾。视频仍只用首帧图生成，
# 分镜图是给人看的连环画，也可以单独重试补生成。

BOARD_BEATS: list[tuple[int, str, str]] = [
    (1, "开场", "动作开始瞬间"),
    (2, "进行", "动作进行到一半，人物姿态处于动作中间状态"),
    (3, "高潮", "动作最激烈的一瞬间"),
    (4, "收尾", "动作结束后的状态"),
]
BOARD_LABELS = {beat: label for beat, label, _ in BOARD_BEATS}


def board_count(project: dict[str, Any]) -> int:
    try:
        count = int(project.get("options", {}).get("storyboard_frames") or 3)
    except (TypeError, ValueError):
        count = 3
    return max(1, min(4, count))


def _board_prompt(project: dict[str, Any], shot: dict[str, Any], beat: int) -> str:
    original = shot.get("prompt") or (
        f"场景：{shot.get('scene', '')}。动作：{shot.get('action', '')}。"
        f"表情：{shot.get('expression', '')}。运镜：{shot.get('camera', '')}。"
    )
    _, label, marker = BOARD_BEATS[beat - 1]
    return (
        f"漫剧《{project.get('title') or project['id']}》镜头第 {beat} 张分镜图（{label}）。"
        f"{original}\n"
        f"{marker}：{shot.get('action', '')}。表情：{shot.get('expression', '')}。\n"
        "与同一镜头的其他分镜图保持同一场景、同一人物、同一服装、同一光线，"
        "画面清晰，构图完整，严格符合场景、动作和表情。"
    )


def _board_ready(shot: dict[str, Any], beat: int, revision: int) -> bool:
    for panel in shot.get("storyboard") or []:
        if int(panel.get("beat") or 0) != int(beat):
            continue
        if int(panel.get("revision") or 0) != int(revision):
            continue
        if panel.get("status") != "ready":
            return False
        quality = panel.get("quality")
        if isinstance(quality, dict):
            return quality.get("passed") is True
        return True
    return False


def _sync_board_panel1(project_id: str, shot_id: str, revision: int) -> None:
    """把首帧图同步为分镜图第 1 张（开场），不额外生成。"""

    def update(project: dict[str, Any]) -> None:
        _, shot = _find_shot(project, shot_id)
        image = shot.get("image") or {}
        if image.get("status") != "ready" or not image.get("name"):
            return
        panel = {
            "beat": 1,
            "label": BOARD_LABELS[1],
            "name": str(image.get("name") or ""),
            "url": str(image.get("url") or ""),
            "status": "ready",
            "prompt": _image_prompt(project, shot),
            "quality": image.get("quality"),
            "attempts": int(image.get("attempts") or 1),
            "revision": int(revision),
        }
        panels = [
            item for item in (shot.get("storyboard") or [])
            if not (int(item.get("beat") or 0) == 1 and int(item.get("revision") or 0) == int(revision))
        ]
        panels.append(panel)
        panels.sort(key=lambda item: int(item.get("beat") or 0))
        shot["storyboard"] = panels

    drama_store.update_project(project_id, update)


def _record_board_quality(
    project_id: str,
    shot_id: str,
    beat: int,
    revision: int,
    quality: QualityResult,
    attempt: int,
    effective_prompt: str,
) -> None:
    def update(project: dict[str, Any]) -> None:
        _, shot = _find_shot(project, shot_id)
        if int(shot.get("revision") or 1) != revision:
            return
        shot.setdefault("quality_history", []).append({
            "stage": "board",
            "beat": int(beat),
            "revision": revision,
            "attempt": attempt,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "score": quality.score,
            "passed": quality.passed,
            "reasons": list(quality.reasons),
            "checker": quality.checker,
            "effective_prompt": effective_prompt,
            "error": "",
        })
        panels = list(shot.get("storyboard") or [])
        for panel in panels:
            if int(panel.get("beat") or 0) == int(beat) and int(panel.get("revision") or 0) == int(revision):
                panel["status"] = "generating"
                panel["attempts"] = attempt
                break
        else:
            panels.append({
                "beat": int(beat),
                "label": BOARD_LABELS.get(int(beat), ""),
                "name": "",
                "url": "",
                "status": "generating",
                "prompt": effective_prompt,
                "quality": None,
                "attempts": attempt,
                "revision": int(revision),
            })
            panels.sort(key=lambda item: int(item.get("beat") or 0))
        shot["storyboard"] = panels

    drama_store.update_project(project_id, update)


def _accept_board(
    project_id: str,
    shot_id: str,
    beat: int,
    revision: int,
    media: GeneratedMedia,
    quality: QualityResult,
    attempts: int,
    prompt: str,
) -> bool:
    from app.services import ai_drama

    suffix = media.path.suffix if media.path.suffix else ".png"
    name = f"{shot_id}_r{revision}_board{beat}{suffix}"
    target = ai_drama._assets(project_id) / name
    try:
        shutil.copyfile(media.path, target)
        return drama_store.accept_board(project_id, shot_id, beat, revision, {
            "beat": int(beat),
            "label": BOARD_LABELS.get(int(beat), ""),
            "name": name,
            "path": str(target),
            "status": "ready",
            "prompt": prompt,
            "quality": quality.as_dict(),
            "attempts": attempts,
        })
    finally:
        media.path.unlink(missing_ok=True)


def _run_board_loop(project_id: str, shot_id: str, should_cancel: CancelCheck) -> None:
    project = drama_store.load_project(project_id)
    _, shot = _find_shot(project, shot_id)
    revision = int(shot.get("revision") or 1)
    count = board_count(project)
    if not shot_image_eligible(shot, revision):
        raise DramaPipelineError(f"镜头 {shot_id} 首帧尚未通过质量门")
    _sync_board_panel1(project_id, shot_id, revision)
    if count <= 1:
        return
    references = _image_references(project, shot)
    threshold = int(project.get("options", {}).get("quality_threshold") or 70)
    for beat in range(2, count + 1):
        project = drama_store.load_project(project_id)
        _, shot = _find_shot(project, shot_id)
        if _board_ready(shot, beat, revision):
            continue
        if should_cancel():
            raise GenerationCancelled("生成已取消")
        prompt = _board_prompt(project, shot, beat)
        attempts = 0

        def generate(effective_prompt: str, prompt=prompt) -> GeneratedMedia:
            return media_generation.generate_image_asset(
                effective_prompt,
                size="2K",
                ratio=str(project["options"].get("aspect_ratio") or "9:16"),
                images=references,
                should_cancel=should_cancel,
            )

        def score(media: GeneratedMedia, prompt=prompt) -> QualityResult:
            return media_quality.score_image(media.path, prompt, references, threshold)

        def record(quality: QualityResult, attempt: int, effective_prompt: str, beat=beat) -> None:
            nonlocal attempts
            attempts = attempt
            _record_board_quality(
                project_id, shot_id, beat, revision, quality, attempt, effective_prompt
            )

        media, quality = run_quality_loop(
            prompt=prompt,
            generate=generate,
            score=score,
            threshold=threshold,
            should_cancel=should_cancel,
            record=record,
        )
        _accept_board(project_id, shot_id, beat, revision, media, quality, attempts, prompt)


def _accept_generated(
    project_id: str,
    shot_id: str,
    stage: str,
    revision: int,
    media: GeneratedMedia,
    quality: QualityResult,
    attempts: int,
) -> bool:
    from app.services import ai_drama

    suffix = media.path.suffix if media.path.suffix else (".jpg" if stage == "image" else ".mp4")
    name = f"{shot_id}_r{revision}_{stage}{suffix}"
    target = ai_drama._assets(project_id) / name
    try:
        shutil.copyfile(media.path, target)
        return drama_store.accept_artifact(project_id, shot_id, stage, revision, {
            "name": name,
            "path": str(target),
            "status": "ready",
            "quality": quality.as_dict(),
            "attempts": attempts,
        })
    finally:
        media.path.unlink(missing_ok=True)


def _run_image_loop(project_id: str, shot_id: str, should_cancel: CancelCheck) -> None:
    project = drama_store.load_project(project_id)
    _, shot = _find_shot(project, shot_id)
    revision = int(shot.get("revision") or 1)
    if shot_image_eligible(shot, revision):
        return
    prompt = _image_prompt(project, shot)
    references = _image_references(project, shot)
    threshold = int(project.get("options", {}).get("quality_threshold") or 70)
    attempts = 0

    def generate(effective_prompt: str) -> GeneratedMedia:
        return media_generation.generate_image_asset(
            effective_prompt,
            size="2K",
            ratio=str(project["options"].get("aspect_ratio") or "9:16"),
            images=references,
            should_cancel=should_cancel,
        )

    def score(media: GeneratedMedia) -> QualityResult:
        return media_quality.score_image(media.path, prompt, references, threshold)

    def record(quality: QualityResult, attempt: int, effective_prompt: str) -> None:
        nonlocal attempts
        attempts = attempt
        _record_quality(
            project_id, shot_id, "image", revision, quality, attempt, effective_prompt
        )

    media, quality = run_quality_loop(
        prompt=prompt,
        generate=generate,
        score=score,
        threshold=threshold,
        should_cancel=should_cancel,
        record=record,
    )
    _accept_generated(project_id, shot_id, "image", revision, media, quality, attempts)
    # 首帧就绪后顺手补齐本镜的连环画分镜图（第 2 张起新生成）
    _sync_board_panel1(project_id, shot_id, revision)
    _run_board_loop(project_id, shot_id, should_cancel)


def _run_video_loop(project_id: str, shot_id: str, should_cancel: CancelCheck) -> None:
    project = drama_store.load_project(project_id)
    _, shot = _find_shot(project, shot_id)
    revision = int(shot.get("revision") or 1)
    if shot_video_eligible(shot, revision):
        return
    if not shot_image_eligible(shot, revision):
        raise DramaPipelineError(f"镜头 {shot_id} 首帧尚未通过质量门")
    from app.services import ai_drama

    frame_path = ai_drama._assets(project_id) / str(shot["image"].get("name") or "")
    if not frame_path.is_file():
        raise DramaPipelineError(f"镜头 {shot_id} 首帧文件缺失")
    jpeg_path = ai_drama._assets(project_id) / f"{shot_id}_r{revision}_video_frame.jpg"
    first_frame = ai_drama._data_uri(ai_drama._to_jpeg(frame_path, jpeg_path))
    prompt = _video_prompt(shot)
    threshold = int(project.get("options", {}).get("quality_threshold") or 70)
    attempts = 0

    def generate(effective_prompt: str) -> GeneratedMedia:
        return media_generation.generate_video_asset(
            effective_prompt,
            int(shot["seconds"]),
            aspect_ratio=str(project["options"].get("aspect_ratio") or "9:16"),
            first_frame=first_frame,
            should_cancel=should_cancel,
        )

    def score(media: GeneratedMedia) -> QualityResult:
        return media_quality.score_video(media.path, prompt, str(frame_path), threshold)

    def record(quality: QualityResult, attempt: int, effective_prompt: str) -> None:
        nonlocal attempts
        attempts = attempt
        _record_quality(
            project_id, shot_id, "video", revision, quality, attempt, effective_prompt
        )

    media, quality = run_quality_loop(
        prompt=prompt,
        generate=generate,
        score=score,
        threshold=threshold,
        should_cancel=should_cancel,
        record=record,
    )
    _accept_generated(project_id, shot_id, "video", revision, media, quality, attempts)


def run_shot(
    project_id: str, shot_id: str, stage: str, *, resume_parent: bool = False
) -> None:
    if stage not in {"image", "video", "all", "board"}:
        raise DramaPipelineError("镜头重试阶段不合法")
    runtime: ShotRuntime | None = None
    revision: int | None = None

    def should_cancel() -> bool:
        return bool(runtime and runtime.cancel.is_set()) or drama_store.project_cancelled(project_id)

    try:
        runtime = _register_shot_runtime(project_id, shot_id)
    except DramaPipelineError:
        # 分镜图补生成是轻量操作：镜头正忙（比如正在重试视频）就跳过，
        # 不把镜头标记为失败，之后可单独再补。
        if stage == "board":
            return
        raise
    try:
        current = drama_store.load_project(project_id)
        _, shot = _find_shot(current, shot_id)
        revision = int(shot.get("revision") or 1)
        if stage in {"image", "all"}:
            _run_image_loop(project_id, shot_id, should_cancel)
        if stage == "board":
            _run_board_loop(project_id, shot_id, should_cancel)
        if not should_cancel() and stage in {"video", "all"}:
            _run_video_loop(project_id, shot_id, should_cancel)
        latest = drama_store.load_project(project_id)
        _, shot = _find_shot(latest, shot_id)
        if (
            resume_parent
            and revision == int(shot.get("revision") or 1)
            and not should_cancel()
        ):
            from app.services import ai_drama

            ai_drama.request_orchestration(project_id)
    except GenerationCancelled:
        drama_store.mark_shot_cancelled(
            project_id, shot_id, expected_revision=revision
        )
    except (DramaPipelineError, QualityUnavailable, MediaAIError, OSError) as exc:
        drama_store.mark_shot_failed(
            project_id, shot_id, str(exc), recoverable=True, expected_revision=revision
        )
    except Exception as exc:  # noqa: BLE001 - 后台线程必须把意外错误持久化为可恢复状态
        message = str(exc) or exc.__class__.__name__
        drama_store.mark_shot_failed(
            project_id, shot_id, message, recoverable=True, expected_revision=revision
        )
    finally:
        if runtime is not None:
            _unregister_shot_runtime(project_id, shot_id, runtime)


def _spawn_shot(
    project_id: str, shot_id: str, stage: str, *, resume_parent: bool = True
) -> threading.Thread:
    worker = threading.Thread(
        target=run_shot,
        args=(project_id, shot_id, stage),
        kwargs={"resume_parent": resume_parent},
        daemon=True,
    )
    worker.start()
    return worker


def run_video_workers(project_id: str, shots: list[dict]) -> None:
    workers = [
        _spawn_shot(project_id, shot["id"], "video", resume_parent=False)
        for shot in shots
    ]
    while workers:
        for worker in list(workers):
            worker.join(timeout=0.2)
            if not worker.is_alive():
                workers.remove(worker)
        if workers and drama_store.project_cancelled(project_id):
            _cancel_all_shots(project_id)


def _all_shots(project: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        shot
        for episode in project.get("episodes") or []
        for shot in episode.get("shots") or []
    ]


def _check_stage_result(project_id: str, stage: str) -> None:
    if drama_store.project_cancelled(project_id):
        raise GenerationCancelled("项目已取消")
    project = drama_store.load_project(project_id)
    shots = _all_shots(project)
    if any(shot.get("status") == "cancelled" for shot in shots):
        raise WaitingReview("有镜头已取消，等待修改或重试")
    eligible = shot_image_eligible if stage == "image" else shot_video_eligible
    if all(eligible(shot, int(shot.get("revision") or 1)) for shot in shots):
        return
    raise DramaPipelineError(f"有镜头{('图片' if stage == 'image' else '视频')}尚未通过质量门")


def _run_image_stage(project_id: str) -> None:
    project = drama_store.load_project(project_id)
    for shot in _all_shots(project):
        if shot.get("status") == "cancelled":
            continue
        if not shot_image_eligible(shot, int(shot.get("revision") or 1)):
            run_shot(project_id, shot["id"], "image")
    _check_stage_result(project_id, "image")


def _run_video_stage(project_id: str) -> None:
    project = drama_store.load_project(project_id)
    shots = _all_shots(project)
    active_shots = [shot for shot in shots if shot.get("status") != "cancelled"]
    if not all(shot_image_eligible(shot, int(shot.get("revision") or 1)) for shot in active_shots):
        raise DramaPipelineError("所有镜头图片合格后才能进入视频阶段")
    pending = [
        shot for shot in shots
        if shot.get("status") != "cancelled"
        and not shot_video_eligible(shot, int(shot.get("revision") or 1))
    ]
    run_video_workers(project_id, pending)
    _check_stage_result(project_id, "video")


def generate_storyboard(project_id: str) -> dict[str, Any]:
    """给整个项目补生成缺失的连环画分镜图，后台并行跑，跳过正忙的镜头。"""
    project = drama_store.load_project(project_id)
    if project.get("cancel_requested"):
        raise DramaPipelineError("项目已取消，请先续跑项目")
    count = board_count(project)
    targets: list[str] = []
    skipped: list[str] = []
    for shot in _all_shots(project):
        if shot.get("status") == "cancelled":
            continue
        revision = int(shot.get("revision") or 1)
        if count <= 1 or all(
            _board_ready(shot, beat, revision) for beat in range(1, count + 1)
        ):
            continue
        if (project_id, shot["id"]) in _SHOT_RUNTIMES or not shot_image_eligible(shot, revision):
            skipped.append(str(shot["id"]))
            continue
        targets.append(str(shot["id"]))

    def _background() -> None:
        workers = [
            _spawn_shot(project_id, shot_id, "board", resume_parent=False)
            for shot_id in targets
        ]
        while workers:
            for worker in list(workers):
                worker.join(timeout=0.2)
                if not worker.is_alive():
                    workers.remove(worker)
            if workers and drama_store.project_cancelled(project_id):
                _cancel_all_shots(project_id)

    thread = threading.Thread(target=_background, daemon=True)
    thread.start()
    return {"started": len(targets), "skipped": skipped, "panels_per_shot": count}


def retry_shot(project_id: str, shot_id: str, stage: str = "all") -> threading.Thread:
    if stage not in {"image", "video", "all", "board"}:
        raise DramaPipelineError("镜头重试阶段不合法")
    project = drama_store.load_project(project_id)
    if project.get("cancel_requested"):
        raise DramaPipelineError("项目已取消，请先续跑项目")
    if stage == "board":
        # 只补生成缺失的分镜图：不动版本号，不置旧产物为 stale，
        # 正在重试视频的镜头也不会被打断。
        return _spawn_shot(project_id, shot_id, stage)
    _stop_shot_runtime(project_id, shot_id)

    def reset(project: dict[str, Any]) -> None:
        episode, shot = _find_shot(project, shot_id)
        previous_revision = int(shot.get("revision") or 1)
        shot["status"] = "pending"
        shot["revision"] = previous_revision + 1
        shot["error"] = ""
        shot["recoverable"] = False
        if stage == "video" and shot_image_eligible(shot, previous_revision):
            shot["image"]["revision"] = shot["revision"]
        if stage in {"image", "all"}:
            shot["image"].update({
                "status": "stale", "quality": None, "legacy_accepted": False,
            })
        if stage in {"image", "video", "all"}:
            shot["video"].update({
                "status": "stale", "quality": None, "legacy_accepted": False,
            })
        episode["status"] = "storyboard"
        project["status"] = "running"
        project["error"] = ""
        project["recoverable"] = False

    drama_store.update_project(project_id, reset)
    return _spawn_shot(project_id, shot_id, stage)


def start_project(project_id: str) -> dict[str, Any]:
    from app.services import ai_drama

    return ai_drama.start_project(project_id)


def resume_project(project_id: str) -> dict[str, Any]:
    from app.services import ai_drama

    return ai_drama.resume_project(project_id)
