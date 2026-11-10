"""全局配置：所有路径都可通过环境变量覆盖，方便本地与 Docker 部署。"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/

# 数据目录（视频文件、封面、SQLite）
DATA_DIR = Path(os.environ.get("VIDEO_HUB_DATA_DIR", BASE_DIR.parent / "data")).resolve()
VIDEOS_DIR = DATA_DIR / "videos"
COVERS_DIR = DATA_DIR / "covers"
TMP_DIR = DATA_DIR / "tmp"
DB_PATH = DATA_DIR / "video_hub.db"
# 直播录制输出目录
RECORDINGS_DIR = DATA_DIR / "recordings"
DOUYIN_LIVE_RECORDER_DIR = Path(os.environ.get(
    "VIDEO_HUB_DLR_DIR", BASE_DIR.parent.parent / "DouyinLiveRecorder")).resolve()
DOUYIN_LIVE_RECORDER_PYTHON = Path(os.environ.get(
    "VIDEO_HUB_DLR_PYTHON", DOUYIN_LIVE_RECORDER_DIR / ".venv" / "bin" / "python"))
# yt-dlp 读取本地 Cookie；不再提供自动发布或网页登录。
COOKIES_DIR = Path(os.environ.get("VIDEO_HUB_COOKIES_DIR", DATA_DIR / "cookies")).resolve()
DEFAULT_COOKIE_NAME = os.environ.get("VIDEO_HUB_COOKIE_NAME", "default")
PLATFORM_NAMES = {"douyin": "抖音", "xiaohongshu": "小红书", "kuaishou": "快手", "bilibili": "哔哩哔哩"}
DOWNLOAD_TIMEOUT = int(os.environ.get("VIDEO_HUB_DOWNLOAD_TIMEOUT", "600"))

# CORS 允许的源（逗号分隔；前端默认由同源 nginx 服务，仅本地开发需要跨域）
CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "VIDEO_HUB_CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",") if o.strip()]

# 本地上传文件大小上限（MB）。nginx 侧 client_max_body_size 设 1040m 与之对齐，
# 4096 会先被 nginx 以 413 截断（默认 error page，前端拿不到 JSON 提示）。
MAX_UPLOAD_MB = int(os.environ.get("VIDEO_HUB_MAX_UPLOAD_MB", "1024"))

# AI 智能剪辑（字幕分析用 LLM，OpenAI 兼容协议，可换 Agnes/DeepSeek/通义/小米 MiMo）
AI_BASE_URL = os.environ.get("VIDEO_HUB_AI_BASE_URL", "https://api.xiaomimimo.com/v1")
AI_API_KEY = os.environ.get("VIDEO_HUB_AI_API_KEY", "")
AI_MODEL = os.environ.get("VIDEO_HUB_AI_MODEL", "mimo-v2.5")
VISION_BASE_URL = os.environ.get("VIDEO_HUB_VISION_BASE_URL", AI_BASE_URL).rstrip("/")
VISION_API_KEY = os.environ.get("VIDEO_HUB_VISION_API_KEY", AI_API_KEY)
VISION_MODEL = os.environ.get("VIDEO_HUB_VISION_MODEL", "")
# 语音转文字：默认在后端进程里按需加载 faster-whisper，不另起常驻 ASR。
# VIDEO_HUB_ASR_URL 仅作可选的远端覆盖；留空就走本机分段转写。
WHISPER_MODEL = os.environ.get("VIDEO_HUB_WHISPER_MODEL", "small")
ASR_URL = os.environ.get("VIDEO_HUB_ASR_URL", "").strip()
NARRATO_TASKS_DIR = Path(os.environ.get(
    "VIDEO_HUB_NARRATO_TASKS_DIR", BASE_DIR.parent / "narratoai" / "storage" / "tasks"
)).resolve()

# 成品套装导出仍调用仓库里的 narratoai/engine/export_task.py，用当前解释器按需跑，
# 不再安装或启动独立的解说引擎虚拟环境。
NARRATOAI_ROOT = Path(os.environ.get("VIDEO_HUB_NARRATOAI_ROOT", BASE_DIR.parent / "narratoai")).resolve()
NARRATOAI_PYTHON = Path(os.environ.get("VIDEO_HUB_NARRATOAI_PYTHON", sys.executable)).absolute()
NARRATO_ENGINE_URL = os.environ.get("VIDEO_HUB_NARRATO_ENGINE_URL", "http://127.0.0.1:8601").rstrip("/")
NARRATO_ENGINE_TOKEN = (
    os.environ.get("VIDEO_HUB_NARRATO_ENGINE_TOKEN", "").strip()
    or os.environ.get("NARRATO_ENGINE_TOKEN", "").strip()
)
try:
    NARRATO_ENGINE_TIMEOUT = max(2.0, float(os.environ.get("VIDEO_HUB_NARRATO_ENGINE_TIMEOUT", "8")))
except ValueError:
    NARRATO_ENGINE_TIMEOUT = 8.0

# Agnes 图像/视频模型（OpenAI 兼容媒体接口）
AGNES_MEDIA_BASE_URL = os.environ.get("AGNES_MEDIA_BASE_URL", "https://api.agnes-ai.cn/v1").rstrip("/")
AGNES_IMAGE_MODEL = os.environ.get("AGNES_IMAGE_MODEL", "agnes-image-2.5-flash")
AGNES_VIDEO_MODEL = os.environ.get("AGNES_VIDEO_MODEL", "agnes-video-2.5-flash")
AGNES_VIDEO_QUERY_URL = os.environ.get("AGNES_VIDEO_QUERY_URL", "https://api.agnes-ai.cn/agnesapi").rstrip("/")
# 生成参考图需要给 Agnes 一个公网可访问的 URL
PUBLIC_BASE_URL = os.environ.get("VIDEO_HUB_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
# Agnes 视频创建排队总时限（秒）：此前 _create_video 是 while not cancelled
# 无限重试，队列满时可无限挂起；超过此时限明确失败，不再静默排队。
try:
    AGNES_VIDEO_CREATE_TIMEOUT = int(os.environ.get("VIDEO_HUB_AGNES_VIDEO_CREATE_TIMEOUT", "1800"))
except ValueError:
    AGNES_VIDEO_CREATE_TIMEOUT = 1800


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _bounded_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def douyin_settings() -> dict:
    """抖音一键成片的可调上限。默认竖屏 1080x1920、单条 12–60 秒、最多 10 条。

    生产机内存紧，编码线程默认 1，输出最长边不超过 1080p。
    """
    min_seconds = _bounded_float("VIDEO_HUB_DOUYIN_MIN_SECONDS", 12, 5, 45)
    max_seconds = _bounded_float("VIDEO_HUB_DOUYIN_MAX_SECONDS", 60, 15, 90)
    if max_seconds < min_seconds + 3:
        max_seconds = min(90.0, min_seconds + 3)
    width = _bounded_int("VIDEO_HUB_DOUYIN_WIDTH", 1080, 270, 1080) // 2 * 2
    height = _bounded_int("VIDEO_HUB_DOUYIN_HEIGHT", 1920, 480, 1920) // 2 * 2
    return {
        "max_clips": _bounded_int("VIDEO_HUB_DOUYIN_MAX_CLIPS", 10, 1, 10),
        "min_seconds": min_seconds,
        "max_seconds": max_seconds,
        "width": max(2, width),
        "height": max(2, height),
        "threads": _bounded_int("VIDEO_HUB_DOUYIN_FFMPEG_THREADS", 1, 1, 2),
    }


def ensure_dirs():
    for d in (DATA_DIR, VIDEOS_DIR, COVERS_DIR, TMP_DIR, RECORDINGS_DIR, COOKIES_DIR):
        d.mkdir(parents=True, exist_ok=True)
