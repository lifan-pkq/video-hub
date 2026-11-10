"""Timeline pieces: speed, end-frame freeze, stills, and picture-in-picture.

Plain 1x trims stay on the same graph the exporter already used. Effects add
setpts / atempo / tpad. Missing media is decided by the caller; this module
only renders paths that exist.
"""

from __future__ import annotations

from pathlib import Path

from engine.media import even, ffmpeg


def clamp_speed(value) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return 1.0
    if speed != speed:
        return 1.0
    return max(0.5, min(2.0, speed))


def clamp_freeze(value) -> float:
    try:
        freeze = float(value)
    except (TypeError, ValueError):
        return 0.0
    if freeze != freeze or freeze < 0:
        return 0.0
    return min(30.0, freeze)


def output_seconds(start: float, end: float, speed: float, freeze: float) -> float:
    span = max(0.0, float(end) - float(start))
    return span / clamp_speed(speed) + clamp_freeze(freeze)


def _scale(width: int, height: int) -> str:
    width = even(width)
    height = even(height)
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p"
    )


def _encode_tail(dest: Path) -> list[str]:
    return [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest),
    ]


def render_video_segment(
    src: Path,
    dest: Path,
    *,
    start: float,
    end: float,
    speed: float = 1.0,
    freeze: float = 0.0,
    width: int,
    height: int,
    has_audio: bool,
    overlay: Path | None = None,
    overlay_seconds: float = 0.0,
    job_id: str | None = None,
    should_cancel=None,
) -> None:
    """Cut [start, end], play it at `speed`, then hold the last frame `freeze` seconds."""
    speed = clamp_speed(speed)
    freeze = clamp_freeze(freeze)
    pre = max(0.0, start - 0.5)
    local_start = max(0.0, start - pre)
    local_end = max(local_start + 0.05, end - pre)
    span = local_end - local_start
    play = span / speed
    freeze_part = freeze if freeze >= 0.05 else 0.0
    out_dur = play + freeze_part
    scale = _scale(width, height)
    plain = abs(speed - 1.0) < 0.01 and freeze_part == 0.0 and overlay is None
    if plain:
        _render_plain(
            src, dest,
            pre=pre, local_start=local_start, local_end=local_end, out_dur=span,
            scale=scale, has_audio=has_audio, job_id=job_id, should_cancel=should_cancel,
        )
        return

    vchain = f"trim=start={local_start:.3f}:end={local_end:.3f},setpts=(PTS-STARTPTS)/{speed:.4f}"
    if freeze_part:
        vchain += f",tpad=stop_mode=clone:stop_duration={freeze_part:.3f}"
    vchain += f",{scale}"
    atempo = f",atempo={speed:.4f}" if abs(speed - 1.0) >= 0.01 else ""
    apad = f",apad=pad_dur={freeze_part:.3f}" if freeze_part else ""
    achain = (
        f"atrim=start={local_start:.3f}:end={local_end:.3f},asetpts=PTS-STARTPTS"
        f"{atempo},aformat=sample_rates=44100:channel_layouts=stereo{apad}"
    )
    use_overlay = overlay is not None and overlay.is_file() and overlay_seconds >= 0.05
    if use_overlay:
        ov = min(float(overlay_seconds), out_dur)
        thumb = even(max(64, int(width * 0.32)))
        graph = (
            f"[0:v]{vchain}[base];"
            f"[1:v]scale={thumb}:-2,format=yuv420p[img];"
            f"[base][img]overlay=W-w-24:H-h-24:enable='between(t,0,{ov:.3f})'[v];"
        )
        if has_audio:
            graph += f"[0:a]{achain}[a]"
            ffmpeg(
                ["-ss", f"{pre:.3f}", "-i", str(src), "-loop", "1", "-i", str(overlay),
                 "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{out_dur:.3f}",
                 *_encode_tail(dest)],
                timeout=900, job_id=job_id, should_cancel=should_cancel,
            )
        else:
            ffmpeg(
                ["-ss", f"{pre:.3f}", "-i", str(src), "-loop", "1", "-i", str(overlay),
                 "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                 "-filter_complex", graph, "-map", "[v]", "-map", "2:a:0", "-t", f"{out_dur:.3f}",
                 *_encode_tail(dest)],
                timeout=900, job_id=job_id, should_cancel=should_cancel,
            )
        return
    if has_audio:
        graph = f"[0:v]{vchain}[v];[0:a]{achain}[a]"
        ffmpeg(
            ["-ss", f"{pre:.3f}", "-i", str(src), "-filter_complex", graph,
             "-map", "[v]", "-map", "[a]", "-t", f"{out_dur:.3f}", *_encode_tail(dest)],
            timeout=900, job_id=job_id, should_cancel=should_cancel,
        )
        return
    graph = f"[0:v]{vchain}[v]"
    ffmpeg(
        ["-ss", f"{pre:.3f}", "-i", str(src), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-filter_complex", graph, "-map", "[v]", "-map", "1:a:0", "-t", f"{out_dur:.3f}",
         *_encode_tail(dest)],
        timeout=900, job_id=job_id, should_cancel=should_cancel,
    )


def _render_plain(
    src: Path,
    dest: Path,
    *,
    pre: float,
    local_start: float,
    local_end: float,
    out_dur: float,
    scale: str,
    has_audio: bool,
    job_id: str | None,
    should_cancel,
) -> None:
    if has_audio:
        graph = (
            f"[0:v]trim=start={local_start:.3f}:end={local_end:.3f},setpts=PTS-STARTPTS,{scale}[v];"
            f"[0:a]atrim=start={local_start:.3f}:end={local_end:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=44100:channel_layouts=stereo[a]"
        )
        ffmpeg(
            ["-ss", f"{pre:.3f}", "-i", str(src), "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
             "-t", f"{out_dur:.3f}", *_encode_tail(dest)],
            timeout=900, job_id=job_id, should_cancel=should_cancel,
        )
        return
    graph = f"[0:v]trim=start={local_start:.3f}:end={local_end:.3f},setpts=PTS-STARTPTS,{scale}[v]"
    ffmpeg(
        ["-ss", f"{pre:.3f}", "-i", str(src), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-filter_complex", graph, "-map", "[v]", "-map", "1:a:0", "-shortest", *_encode_tail(dest)],
        timeout=900, job_id=job_id, should_cancel=should_cancel,
    )


def render_image_segment(
    image: Path,
    dest: Path,
    *,
    duration: float,
    width: int,
    height: int,
    job_id: str | None = None,
    should_cancel=None,
) -> None:
    """Full-frame still. Audio is silence; callers can mux a source bed afterwards."""
    duration = max(0.3, min(30.0, float(duration)))
    ffmpeg(
        ["-loop", "1", "-i", str(image), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-vf", _scale(width, height), "-t", f"{duration:.3f}", "-shortest", *_encode_tail(dest)],
        timeout=300, job_id=job_id, should_cancel=should_cancel,
    )


def mux_source_audio(
    picture: Path,
    source: Path,
    dest: Path,
    *,
    start: float,
    end: float,
    speed: float,
    freeze: float,
    job_id: str | None = None,
    should_cancel=None,
) -> None:
    """Replace a still's silence with the source range, sped and padded like the picture."""
    speed = clamp_speed(speed)
    freeze = clamp_freeze(freeze)
    pre = max(0.0, start - 0.5)
    local_start = max(0.0, start - pre)
    local_end = max(local_start + 0.05, end - pre)
    freeze_part = freeze if freeze >= 0.05 else 0.0
    out_dur = (local_end - local_start) / speed + freeze_part
    atempo = f",atempo={speed:.4f}" if abs(speed - 1.0) >= 0.01 else ""
    apad = f",apad=pad_dur={freeze_part:.3f}" if freeze_part else ""
    achain = (
        f"atrim=start={local_start:.3f}:end={local_end:.3f},asetpts=PTS-STARTPTS"
        f"{atempo},aformat=sample_rates=44100:channel_layouts=stereo{apad}"
    )
    ffmpeg(
        ["-i", str(picture), "-ss", f"{pre:.3f}", "-i", str(source),
         "-filter_complex", f"[1:a]{achain}[a]", "-map", "0:v:0", "-map", "[a]",
         "-t", f"{out_dur:.3f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(dest)],
        timeout=300, job_id=job_id, should_cancel=should_cancel,
    )


def mix_narration(
    picture: Path,
    narration: Path,
    dest: Path,
    *,
    original_volume: float,
    job_id: str | None = None,
    should_cancel=None,
) -> None:
    """Duck the picture's audio under a narration track. Picture length wins."""
    volume = max(0.0, min(1.0, float(original_volume)))
    if volume <= 0.001:
        graph = "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a]"
    else:
        graph = (
            f"[0:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={volume:.3f}[a0];"
            f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=1[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
    ffmpeg(
        ["-i", str(picture), "-i", str(narration), "-filter_complex", graph,
         "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(dest)],
        timeout=300, job_id=job_id, should_cancel=should_cancel,
    )
