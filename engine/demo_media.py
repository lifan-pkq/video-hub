"""Short synthetic clips so the workbench can be tried without a camera roll."""

from __future__ import annotations

import json
from pathlib import Path

from engine.media import ffmpeg


def _concat_blocks(dest: Path, blocks: list[tuple[str, float, str | None]]) -> None:
    args: list[str] = []
    graph_in = []
    for index, (color, seconds, freq) in enumerate(blocks):
        args += ["-f", "lavfi", "-i", f"color=c={color}:s=960x540:r=25:d={seconds}"]
        if freq:
            args += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={seconds}"]
        else:
            args += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={seconds}"]
        graph_in.append(f"[{index * 2}:v][{index * 2 + 1}:a]")
    graph = "".join(graph_in) + f"concat=n={len(blocks)}:v=1:a=1[v][a]"
    ffmpeg(
        [*args, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(dest)],
        timeout=120,
    )


def write_demo_set(directory: Path) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=True)
    gap = directory / "demo-gap.mp4"
    cuts = directory / "demo-cuts.mp4"
    _concat_blocks(gap, [
        ("0xc4492c", 4, "440"),
        ("0xc4492c", 2.5, None),
        ("black", 2.5, None),
        ("0x1f6f6a", 5, "660"),
    ])
    _concat_blocks(cuts, [
        ("0x2c3e50", 3, "392"),
        ("0x2c3e50", 3, None),
        ("0x8e5a2a", 3, "494"),
        ("0x1d4e4a", 3, None),
    ])
    gap.with_suffix(".transcript.json").write_text(json.dumps({
        "segments": [
            {"id": "t1", "start": 0.3, "end": 3.6, "text": "这是一段演示口播，用来说明切点怎么对齐句子。"},
            {"id": "t2", "start": 9.3, "end": 13.5, "text": "黑场之后画面切到青绿，句子从这里重新开始。"},
        ]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    cuts.with_suffix(".transcript.json").write_text(json.dumps({
        "segments": [
            {"id": "t1", "start": 0.2, "end": 2.7, "text": "第一镜在说话。"},
            {"id": "t2", "start": 6.2, "end": 8.7, "text": "中间跳过一段静音，第三镜继续。"},
        ]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return [
        {"filename": gap.name, "title": "演示 · 静音和黑场", "sidecar": True},
        {"filename": cuts.name, "title": "演示 · 多镜头", "sidecar": True},
    ]
