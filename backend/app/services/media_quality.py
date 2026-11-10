"""生成图片与视频的技术检查和统一视觉质量分。"""

from __future__ import annotations

import base64
import json
import mimetypes
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import TMP_DIR, VISION_MODEL


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


def _valid_image(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
             "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, timeout=60, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _probe_video(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "format=duration:stream=codec_type,width,height",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode != 0:
            return None
        raw = json.loads(result.stdout or "{}")
        stream = next(
            (item for item in raw.get("streams") or [] if item.get("codec_type") == "video"),
            {},
        )
        duration = float((raw.get("format") or {}).get("duration") or 0)
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        if duration <= 0 or width <= 0 or height <= 0:
            return None
        return {"duration": duration, "width": width, "height": height}
    except (OSError, TypeError, ValueError, subprocess.SubprocessError):
        return None


def _frame_at(path: Path, timestamp: float, target: Path) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{timestamp:.3f}",
             "-i", str(path), "-frames:v", "1", "-vf", "scale='min(960,iw)':-2",
             "-q:v", "4", str(target)],
            capture_output=True, timeout=120, check=False,
        )
        return result.returncode == 0 and target.is_file() and target.stat().st_size > 0
    except (OSError, subprocess.SubprocessError):
        return False


def _middle_frames(path: Path, timestamp: float, directory: Path) -> list[Path]:
    target = directory / "middle-%d.jpg"
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{timestamp:.3f}",
             "-i", str(path), "-frames:v", "5", "-vf", "scale='min(960,iw)':-2",
             "-q:v", "4", str(target)],
            capture_output=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [
        frame for index in range(1, 6)
        if (frame := directory / f"middle-{index}.jpg").is_file() and frame.stat().st_size > 0
    ]


def _image_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _reference_url(reference: str) -> str:
    value = str(reference).strip()
    if value.startswith(("data:image/", "http://", "https://")):
        return value
    return _image_data_uri(Path(value))


def _normalize_vision_result(raw: dict[str, Any]) -> tuple[int, list[str]]:
    if not isinstance(raw, dict) or "score" not in raw or "reasons" not in raw:
        raise QualityUnavailable("视觉模型必须返回 score 和 reasons")
    if isinstance(raw["score"], bool) or not isinstance(raw["reasons"], list):
        raise QualityUnavailable("视觉模型返回的 score 或 reasons 格式无效")
    try:
        score = max(0, min(100, int(float(raw["score"]))))
    except (TypeError, ValueError, OverflowError) as exc:
        raise QualityUnavailable("视觉模型返回的 score 格式无效") from exc
    return score, [str(item) for item in raw["reasons"]]


def _vision_score(
    frames: list[Path], prompt: str, references: list[str], kind: str
) -> dict[str, Any]:
    if not VISION_MODEL:
        raise QualityUnavailable("未配置视觉模型")
    dimensions = (
        "提示词符合度、人物与参考图一致性、构图、清晰度、明显畸形和画面缺陷"
        if kind == "image"
        else "首帧一致性、人物与服装稳定性、场景连续性、动作提示符合度和明显画面异常"
    )
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            f"请对{('图片' if kind == 'image' else '视频抽帧')}进行满分 100 的质量评分。"
            f"评分维度：{dimensions}。\n原始提示词：{prompt}\n"
            f"参考图共 {len(references)} 张，先给出参考图，再按时间顺序给出待评分画面。"
            "低分必须给出具体、可用于修改提示词的原因。只输出 JSON："
            '{"score":0,"reasons":[]}'
        ),
    }]
    try:
        for reference in references:
            content.append({"type": "image_url", "image_url": {"url": _reference_url(reference)}})
        for frame in frames:
            content.append({"type": "image_url", "image_url": {"url": _image_data_uri(frame)}})
        from app.services.understanding import _request_json

        raw = _request_json([{"role": "user", "content": content}], vision=True)
        score, reasons = _normalize_vision_result(raw)
        return {"score": score, "reasons": reasons}
    except QualityUnavailable:
        raise
    except Exception as exc:
        raise QualityUnavailable(f"视觉质量评分不可用：{exc}") from exc


def _scored_result(raw: dict[str, Any], threshold: int) -> QualityResult:
    score, reasons = _normalize_vision_result(raw)
    return QualityResult(score, score >= threshold, reasons, "vision")


def score_image(path: Path, prompt: str, references: list[str], threshold: int) -> QualityResult:
    if not _valid_image(path):
        return QualityResult(0, False, ["图片不存在、为空或无法解码"], "technical")
    try:
        raw = _vision_score([path], prompt, references, kind="image")
        return _scored_result(raw, threshold)
    except QualityUnavailable:
        raise
    except Exception as exc:
        raise QualityUnavailable(str(exc)) from exc


def score_video(
    path: Path, prompt: str, reference_image: str | None, threshold: int
) -> QualityResult:
    info = _probe_video(path)
    if info is None:
        return QualityResult(0, False, ["视频不存在、为空、元数据无效或无法解码"], "technical")
    duration = float(info["duration"])
    middle = duration / 2
    timestamps = [0.0, round(middle, 3), round(max(0.0, duration - 0.05), 3)]
    with tempfile.TemporaryDirectory(prefix="media-quality-", dir=TMP_DIR) as temp:
        frames: list[Path] = []
        for index, timestamp in enumerate(timestamps):
            frame = Path(temp) / f"frame-{index}.jpg"
            if not _frame_at(path, timestamp, frame):
                return QualityResult(0, False, ["视频无法解码或关键帧抽取失败"], "technical")
            frames.append(frame)
        sequential = _middle_frames(path, middle, Path(temp))
        if not sequential:
            return QualityResult(0, False, ["视频无法解码或中段帧抽取失败"], "technical")
        frames.extend(sequential)
        try:
            raw = _vision_score(
                frames, prompt, [reference_image] if reference_image else [], kind="video"
            )
            return _scored_result(raw, threshold)
        except QualityUnavailable:
            raise
        except Exception as exc:
            raise QualityUnavailable(str(exc)) from exc
