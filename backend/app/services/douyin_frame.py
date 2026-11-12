"""竖屏裁切和播客式铺满。只算 ffmpeg 滤镜，不读文件。"""
from __future__ import annotations

def portrait_crop_size(src_w: int, src_h: int) -> tuple[int, int]:
    """在原画面里取一块 9:16，边长取偶数，方便 yuv420。"""
    src_w = max(2, int(src_w))
    src_h = max(2, int(src_h))
    ratio = 9 / 16
    if src_w / src_h > ratio:
        crop_h = src_h - src_h % 2
        crop_w = int(src_h * ratio) // 2 * 2
    else:
        crop_w = src_w - src_w % 2
        crop_h = int(src_w / ratio) // 2 * 2
    crop_w = max(2, min(src_w - src_w % 2, crop_w))
    crop_h = max(2, min(src_h - src_h % 2, crop_h))
    return crop_w, crop_h


def crop_origin(src_w: int, src_h: int, crop_w: int, crop_h: int, box: dict | None) -> tuple[int, int]:
    """subject_box 用 0–1000 归一化坐标。没有主体时居中。"""
    if not box:
        return (src_w - crop_w) // 2, (src_h - crop_h) // 2
    try:
        center_x = (float(box.get("x", 500)) + float(box.get("width", 0)) / 2) / 1000 * src_w
        center_y = (float(box.get("y", 500)) + float(box.get("height", 0)) / 2) / 1000 * src_h
    except (TypeError, ValueError):
        return (src_w - crop_w) // 2, (src_h - crop_h) // 2
    x = int(round(center_x - crop_w / 2))
    y = int(round(center_y - crop_h / 2))
    return max(0, min(src_w - crop_w, x)), max(0, min(src_h - crop_h, y))


def _piecewise(steps: list[tuple[float, str]], fallback: str) -> str:
    expression = steps[-1][1]
    for index in range(len(steps) - 2, -1, -1):
        expression = f"if(lt(t,{steps[index + 1][0]:.3f}),{steps[index][1]},{expression})"
    return f"if(lt(t,{steps[0][0]:.3f}),{fallback},{expression})"


def vertical_filter(
    src_w: int,
    src_h: int,
    track: list[dict] | None,
    out_w: int,
    out_h: int,
    layout: str = "interview",
) -> tuple[str, str]:
    """返回 ffmpeg 滤镜和 framing。

    interview：subject（跟着说话人）或 center。
    podcast：完整画面居中，模糊背景铺满 9:16，不再按主体裁切。
    """
    if layout == "podcast":
        even_w = max(2, int(out_w) // 2 * 2)
        even_h = max(2, int(out_h) // 2 * 2)
        graph = (
            f"split[bg][fg];"
            f"[bg]scale={even_w}:{even_h}:force_original_aspect_ratio=increase,"
            f"crop={even_w}:{even_h},boxblur=20:2[bg2];"
            f"[fg]scale={even_w}:{even_h}:force_original_aspect_ratio=decrease:"
            f"force_divisible_by=2[fg2];"
            f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
        return graph, "podcast"
    crop_w, crop_h = portrait_crop_size(src_w, src_h)
    points = [item for item in (track or []) if isinstance(item, dict) and isinstance(item.get("subject_box"), dict)]
    if not points:
        x, y = crop_origin(src_w, src_h, crop_w, crop_h, None)
        crop = f"crop={crop_w}:{crop_h}:{x}:{y}"
        mode = "center"
    elif len(points) == 1:
        x, y = crop_origin(src_w, src_h, crop_w, crop_h, points[0].get("subject_box"))
        crop = f"crop={crop_w}:{crop_h}:{x}:{y}"
        mode = "subject"
    else:
        xs: list[tuple[float, str]] = []
        ys: list[tuple[float, str]] = []
        for point in sorted(points, key=lambda item: float(item.get("timestamp") or 0)):
            x, y = crop_origin(src_w, src_h, crop_w, crop_h, point.get("subject_box"))
            stamp = max(0.0, float(point.get("timestamp") or 0))
            xs.append((stamp, str(x)))
            ys.append((stamp, str(y)))
        fallback_x, fallback_y = crop_origin(src_w, src_h, crop_w, crop_h, None)
        crop = (
            f"crop={crop_w}:{crop_h}:x='{_piecewise(xs, str(fallback_x))}':"
            f"y='{_piecewise(ys, str(fallback_y))}'"
        )
        mode = "subject"
    even_w = max(2, int(out_w) // 2 * 2)
    even_h = max(2, int(out_h) // 2 * 2)
    return f"{crop},scale={even_w}:{even_h}:flags=bilinear,setsar=1", mode


