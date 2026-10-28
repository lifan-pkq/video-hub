"""直播录制：解析直播流地址（抖音/B站/yt-dlp 兜底）+ ffmpeg 子进程录制。

- 抖音：优先通过 DouyinLiveRecorder 接口取流，直播间页面解析兜底
- B 站：公开 API 取流地址；其他平台/直接流地址：yt-dlp 解析或直通
- 录制用 ffmpeg -c copy（零转码直接写 mp4），stdin 写 q 优雅停止（保证 moov 完整）
- 录制完成自动入库视频库（探测时长/分辨率 + 生成封面）
- 状态持久化在 recordings 表；服务重启后由 main.py 把 recording 标记为 failed
"""
import json
import re
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from app.config import COVERS_DIR, RECORDINGS_DIR, TMP_DIR, VIDEOS_DIR
from app.db import (add_recording, add_video, get_recording,
                    update_recording)
from app.services.media import make_cover, probe_video

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# 画质档位：best 原画 / high 高清 / medium 标清 / low 流畅
QUALITY_LEVELS = ("best", "high", "medium", "low")
# 抖音 flv_pull_url / hls_pull_url_map 的键优先级
DOUYIN_QUALITY_KEYS = {
    "best": ["FULL_HD1", "HD1", "SD1", "SD2"],
    "high": ["HD1", "SD1", "SD2", "FULL_HD1"],
    "medium": ["SD1", "SD2", "HD1"],
    "low": ["SD2", "SD1"],
}
# B站 playUrl 的 qn 参数
BILI_QUALITY_QN = {"best": 10000, "high": 400, "medium": 150, "low": 80}
# yt-dlp 兜底的高度限制
YTDLP_FORMAT = {
    "best": "best",
    "high": "best[height<=1080]/best",
    "medium": "best[height<=720]/best",
    "low": "best[height<=480]/best",
}


class LiveOfflineError(RuntimeError):
    pass


_lock = threading.Lock()
_procs: dict[int, subprocess.Popen] = {}     # rec_id -> ffmpeg 进程
_finalized: set[int] = set()                 # 已开始/完成收尾的 rec_id（防 _monitor 与 stop 并发重复入库）
_finalize_events: dict[int, threading.Event] = {}  # 收尾完成信号，stop 接口等它拿到准确结果


def detect_platform(url: str) -> str:
    if re.search(r"(douyin|iesdouyin|webcast\.amemv)", url):
        return "douyin"
    if re.search(r"(bilibili|b23\.tv)", url):
        return "bilibili"
    if re.search(r"(kuaishou|gifshow)", url):
        return "kuaishou"
    if re.search(r"\.(m3u8|flv|ts)(\?|$)", url, re.I):
        return "direct"
    return "unknown"


def extract_url(text: str) -> str:
    """从分享文案里抠出第一个 http 链接。"""
    m = re.search(r"https?://[^\s，。,>'\"）)】]+", text)
    return m.group(0) if m else text.strip()


def _http_get(url: str, referer: str | None = None, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        **({"Referer": referer} if referer else {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _resolve_douyin_with_dlr(url: str, quality: str) -> str:
    from app.config import DOUYIN_LIVE_RECORDER_DIR, DOUYIN_LIVE_RECORDER_PYTHON

    script = r'''
import asyncio
import json
import sys
from src import spider, stream

async def main():
    data = await spider.get_douyin_app_stream_data(sys.argv[1])
    info = await stream.get_douyin_stream_url(data, sys.argv[2], None)
    if not info.get("is_live"):
        raise RuntimeError("该直播间当前未开播")
    print(json.dumps({"url": info.get("flv_url") or info.get("record_url")}))

asyncio.run(main())
'''
    quality_name = {"best": "OD", "high": "HD", "medium": "SD", "low": "LD"}.get(quality, "OD")
    result = subprocess.run(
        [str(DOUYIN_LIVE_RECORDER_PYTHON), "-c", script, url, quality_name],
        cwd=DOUYIN_LIVE_RECORDER_DIR, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        message = lines[-1][:200] if lines else "抖音接口解析失败"
        if "该直播间当前未开播" in message:
            raise LiveOfflineError("该直播间当前未开播")
        raise RuntimeError(message)
    stream_url = json.loads(result.stdout.strip().splitlines()[-1]).get("url")
    if not stream_url:
        raise RuntimeError("抖音接口未返回直播流地址")
    return stream_url


def _resolve_douyin(url: str, quality: str = "best") -> str:
    try:
        return _resolve_douyin_with_dlr(url, quality)
    except LiveOfflineError:
        raise
    except Exception:
        pass
    html = _http_get(url, referer="https://live.douyin.com/")
    # 途径一：SSR 注水数据里的结构化流地址（按画质档位选键）
    m = re.search(r"<script[^>]*>window\._SSR_HYDRATED_DATA=(.*?)</script>", html, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            room = data["app"]["initialState"]["roomStore"]["roomInfo"]["room"]
            stream = room.get("stream_url") or {}
            for key in DOUYIN_QUALITY_KEYS.get(quality, DOUYIN_QUALITY_KEYS["best"]):
                for source in (stream.get("flv_pull_url") or {}, stream.get("hls_pull_url_map") or {}):
                    candidate = source.get(key)
                    if candidate:
                        return candidate
        except Exception:
            pass
    # 途径二：正则从 HTML 抠流地址，按画质打分取最高
    urls = set(re.findall(r'https?://[^"\'\\]+?\.(?:flv|m3u8)(?:\?[^"\'\\]*)?', html))
    if not urls:
        raise RuntimeError("未能在页面中找到直播流地址（可能未开播或页面结构已变）")

    def score(u: str) -> int:
        for kw, s in (("_uhd", 6), ("_or4", 5), ("_hd", 4), (".flv", 3), ("_sd", 2), (".m3u8", 1)):
            if kw in u:
                return s
        return 0

    return max(urls, key=score)


def _bilibili_room_id(url: str) -> str:
    m = re.search(r"live\.bilibili\.com/(?:[a-z]+/)?(\d+)", url)
    if not m:
        # b23.tv 等短链：跟随重定向拿真实直播间地址
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                url = resp.url
        except Exception as e:
            raise RuntimeError(f"B 站短链解析失败: {e}")
        m = re.search(r"live\.bilibili\.com/(?:[a-z]+/)?(\d+)", url)
    if not m:
        raise RuntimeError("无法从链接中解析 B 站房间号")
    return m.group(1)


def _resolve_bilibili(url: str, quality: str = "best") -> str:
    room_id = _bilibili_room_id(url)
    # playUrl 对未开播的房间也会返回（失效的）durl，先查开播状态再取流
    try:
        info = json.loads(_http_get(
            f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}"))
        if (info.get("data") or {}).get("live_status") != 1:
            raise LiveOfflineError("该直播间当前未开播")
    except RuntimeError:
        raise
    except Exception:
        pass  # 状态查询失败不阻塞，继续尝试取流
    qn = BILI_QUALITY_QN.get(quality, 10000)
    text = _http_get(f"https://api.live.bilibili.com/room/v1/Room/playUrl"
                     f"?cid={room_id}&quality={qn}&platform=web")
    data = json.loads(text)
    durl = (data.get("data") or {}).get("durl") or []
    if not durl:
        raise RuntimeError("B 站接口未返回流地址（可能未开播）")
    return durl[0]["url"]


def _resolve_ytdlp(url: str, quality: str = "best") -> str:
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True,
                           "format": YTDLP_FORMAT.get(quality, "best")}) as ydl:
        info = ydl.extract_info(url, download=False)
    stream = info.get("url")
    if not stream:
        raise RuntimeError("yt-dlp 未能解析出流地址")
    return stream


def resolve_stream_url(url: str, platform: str, quality: str = "best") -> str:
    if platform == "direct":
        stream_url = url
    elif platform == "douyin":
        stream_url = _resolve_douyin(url, quality)
    elif platform == "bilibili":
        stream_url = _resolve_bilibili(url, quality)
    else:
        stream_url = _resolve_ytdlp(url, quality)
    return stream_url


def _safe_name(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", text.strip())
    return text[:60] or "live"


def _build_ffmpeg_cmd(stream_url: str, platform: str, out_path: Path, fps: int = 0) -> list[str]:
    opts = ["-fflags", "+discardcorrupt"]
    if re.search(r"\.(m3u8|flv|ts)(\?|$)", stream_url, re.I):
        opts += ["-reconnect", "1", "-reconnect_streamed", "1",
                 "-reconnect_on_network_error", "1",
                 "-reconnect_on_http_error", "4xx,5xx",
                 "-reconnect_delay_max", "60",
                 "-timeout", "10000000"]
    if platform == "douyin":
        mobile_ua = ("Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/14.2 "
                     "Chrome/87.0.4280.141 Mobile Safari/537.36")
        opts += ["-headers", f"User-Agent: {mobile_ua}\r\nReferer: https://live.douyin.com/\r\n"]
    if platform == "bilibili":
        opts += ["-headers", f"User-Agent: {UA}\r\nReferer: https://live.bilibili.com/\r\n"]
    # 帧率限制需要重编码（流拷贝改不了帧率）；0 = 原始帧率直接拷贝
    # 注意：视频重编码时音频必须一起转 aac——copy 音频进 fMP4 会被 muxer 拒绝
    if fps > 0:
        codec = ["-vf", f"fps={fps}", "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "23", "-c:a", "aac", "-b:a", "128k"]
    else:
        # HLS 来的 AAC 是 ADTS 裸流，fMP4 封装前必须转 ASC，否则 muxer 报 Operation not permitted
        codec = ["-c", "copy", "-bsf:a", "aac_adtstoasc"]
    # 写成分片 mp4（fMP4）：录制中即可被预览播放；收尾时再 remux 成 faststart
    movflags = "+frag_keyframe+empty_moov+default_base_moof"
    return ["ffmpeg", "-y", *opts, "-i", stream_url,
            *codec, "-f", "mp4", "-movflags", movflags, str(out_path)]


def start_recording(url: str, title: str = "", quality: str = "best", fps: int = 0) -> dict:
    """创建录制任务并拉起 ffmpeg。失败会抛异常（路由层转 400）。"""
    url = extract_url(url)
    platform = detect_platform(url)
    if quality not in QUALITY_LEVELS:
        quality = "best"
    stream_url = resolve_stream_url(url, platform, quality)

    if not title:
        title = f"直播录制 {time.strftime('%m-%d %H:%M')}"
    filename = f"{_safe_name(title)}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}.mp4"
    out_path = RECORDINGS_DIR / filename

    rec_id = add_recording(url, platform, title, filename, quality, fps)
    log_path = TMP_DIR / f"record_{rec_id}.log"
    cmd = _build_ffmpeg_cmd(stream_url, platform, out_path, fps)
    log_f = open(log_path, "wb")
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=log_f)
    except Exception as e:
        log_f.close()
        update_recording(rec_id, status="failed", error=str(e),
                         finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        raise RuntimeError(f"ffmpeg 启动失败: {e}")

    with _lock:
        _procs[rec_id] = proc
    threading.Thread(target=_monitor, args=(rec_id, proc, log_f), daemon=True).start()
    return get_recording(rec_id)


def _monitor(rec_id: int, proc: subprocess.Popen, log_f):
    rec = None
    try:
        proc.wait()
        rec = get_recording(rec_id)
        if not rec or rec["status"] != "recording":
            return  # 已被 stop_recording 处理

        size = _file_size(rec["filename"])
        if proc.returncode == 0 and size > 0:
            _finish_success(rec_id, rec)
        else:
            err = _last_error_line(rec_id)
            update_recording(rec_id, status="failed", size=size,
                             error=f"ffmpeg 退出码 {proc.returncode}: {err}",
                             finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        size = _file_size(rec["filename"]) if rec else 0
        update_recording(rec_id, status="failed", size=size,
                         error=f"录制收尾失败: {e}",
                         finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    finally:
        try:
            log_f.close()
        except Exception:
            pass
        with _lock:
            _procs.pop(rec_id, None)


def _probe_with_retry(path: Path, tries: int = 5, interval: float = 1.0) -> dict:
    """ffmpeg 刚退出时文件可能仍在收尾（faststart 重写），探测失败重试几次。"""
    for i in range(tries):
        info = probe_video(path)
        if info.get("duration"):
            return info
        time.sleep(interval)
    return {}


def _remux_to_faststart(src: Path, dst: Path) -> bool:
    """把 fMP4 录制文件 remux 成普通 faststart mp4（入库后拖动进度更稳）。失败返回 False。"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-c", "copy",
             "-movflags", "+faststart", str(dst)],
            capture_output=True, timeout=600,
        )
        return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    except Exception:
        return False


def _finish_success(rec_id: int, rec: dict) -> bool:
    """录制收尾：remux + 探测信息 + 挪进视频库 + 封面。全局只允许执行一次，抢到才返回 True。"""
    with _lock:
        if rec_id in _finalized:
            return False
        _finalized.add(rec_id)
        ev = threading.Event()
        _finalize_events[rec_id] = ev
    try:
        size = _file_size(rec["filename"])
        src = RECORDINGS_DIR / rec["filename"]
        dst = VIDEOS_DIR / rec["filename"]
        # fMP4 → faststart remux；失败则直接挪原文件兜底
        if not _remux_to_faststart(src, dst):
            try:
                src.replace(dst)
            except Exception:
                dst = src  # 跨设备等极端情况就留在原地
        else:
            src.unlink(missing_ok=True)
        info = _probe_with_retry(dst)
        cover_name = None
        cover_path = COVERS_DIR / (Path(rec["filename"]).stem + ".jpg")
        if make_cover(dst, cover_path):
            cover_name = cover_path.name
        video = add_video(title=rec["title"], filename=dst.name, size=size,
                          duration=info.get("duration"), width=info.get("width"),
                          height=info.get("height"), cover=cover_name,
                          source_platform=f"live_{rec['platform']}", source_url=rec["url"])
        update_recording(rec_id, status="completed", size=size,
                         duration=info.get("duration"), video_id=video["id"],
                         finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        return True
    finally:
        ev.set()
        with _lock:
            # 收尾完成信号已发出，清理防重复登记的内存结构（rec_id 单调递增，不会复用）
            _finalize_events.pop(rec_id, None)
            _finalized.discard(rec_id)


def stop_recording(rec_id: int) -> bool:
    """向 ffmpeg stdin 写 q 优雅停止；10 秒不退则强杀。返回是否拦到了在录进程。"""
    with _lock:
        proc = _procs.get(rec_id)
    rec = get_recording(rec_id)
    if not rec or rec["status"] != "recording":
        return False
    if proc and proc.poll() is None:
        try:
            proc.stdin.write(b"q")
            proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    size = _file_size(rec["filename"])
    if size > 0:
        # 手动停止也保留已录内容并入库；若监控线程已在收尾，等它拿准确结果
        update_recording(rec_id, status="stopped", size=size)
        rec = get_recording(rec_id)
        if not _finish_success(rec_id, rec):
            with _lock:
                ev = _finalize_events.get(rec_id)
            if ev:
                ev.wait(timeout=20)
        update_recording(rec_id, status="stopped")
    else:
        update_recording(rec_id, status="stopped", size=0,
                         finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    return True


def _file_size(filename: str | None) -> int:
    if not filename:
        return 0
    for base in (RECORDINGS_DIR, VIDEOS_DIR):
        p = base / filename
        if p.exists():
            return p.stat().st_size
    return 0


def _last_error_line(rec_id: int) -> str:
    log = TMP_DIR / f"record_{rec_id}.log"
    try:
        lines = [l for l in log.read_text(errors="replace").splitlines() if l.strip()]
        return lines[-1][:200] if lines else ""
    except Exception:
        return ""


def recording_file_path(rec: dict) -> Path | None:
    if not rec.get("filename"):
        return None
    for base in (RECORDINGS_DIR, VIDEOS_DIR):
        p = base / rec["filename"]
        if p.exists():
            return p
    return None
