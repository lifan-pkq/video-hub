"""全局配置：所有路径都可通过环境变量覆盖，方便本地与 Docker 部署。"""
import os
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
# 下载/作者抓取使用的本地 cookie 目录（不再提供网页登录）
COOKIES_DIR = Path(os.environ.get("VIDEO_HUB_COOKIES_DIR", DATA_DIR / "cookies")).resolve()
DEFAULT_COOKIE_NAME = os.environ.get("VIDEO_HUB_COOKIE_NAME", "default")
# 下载/作者抓取展示用的平台名
PLATFORM_NAMES = {"douyin": "抖音", "xiaohongshu": "小红书", "kuaishou": "快手", "bilibili": "哔哩哔哩"}
DOWNLOAD_TIMEOUT = int(os.environ.get("VIDEO_HUB_DOWNLOAD_TIMEOUT", "600"))

# CORS 允许的源（逗号分隔；前端默认由同源 nginx 服务，仅本地开发需要跨域）
CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "VIDEO_HUB_CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",") if o.strip()]

# 本地上传文件大小上限（MB）
MAX_UPLOAD_MB = int(os.environ.get("VIDEO_HUB_MAX_UPLOAD_MB", "4096"))

# AI 智能剪辑（字幕分析用 LLM，OpenAI 兼容协议，可换 Agnes/DeepSeek/通义/小米 MiMo）
AI_BASE_URL = os.environ.get("VIDEO_HUB_AI_BASE_URL", "https://api.xiaomimimo.com/v1")
AI_API_KEY = os.environ.get("VIDEO_HUB_AI_API_KEY", "")
AI_MODEL = os.environ.get("VIDEO_HUB_AI_MODEL", "mimo-v2.5")
# 语音转文字模型（faster-whisper，本地运行，small 兼顾中文效果与速度）
WHISPER_MODEL = os.environ.get("VIDEO_HUB_WHISPER_MODEL", "small")
# 配置共享 ASR 后不再在 Video Hub 进程内加载 whisper 模型
ASR_URL = os.environ.get("VIDEO_HUB_ASR_URL", "").strip()
NARRATO_TASKS_DIR = Path(os.environ.get(
    "VIDEO_HUB_NARRATO_TASKS_DIR", BASE_DIR.parent / "narratoai" / "storage" / "tasks"
)).resolve()

# AI 解说引擎（narratoai/engine，独立进程，仅监听本机）
NARRATOAI_ROOT = Path(os.environ.get("VIDEO_HUB_NARRATOAI_ROOT", BASE_DIR.parent / "narratoai")).resolve()
# 注意：不要 resolve()，.venv/bin/python 是符号链接，resolve 后会跳出虚拟环境。
NARRATOAI_PYTHON = Path(
    os.environ.get("VIDEO_HUB_NARRATOAI_PYTHON", NARRATOAI_ROOT / ".venv" / "bin" / "python")
).absolute()
NARRATO_ENGINE_URL = os.environ.get("VIDEO_HUB_NARRATO_ENGINE_URL", "http://127.0.0.1:8601").rstrip("/")
NARRATO_ENGINE_TOKEN = (
    os.environ.get("VIDEO_HUB_NARRATO_ENGINE_TOKEN", "").strip()
    or os.environ.get("NARRATO_ENGINE_TOKEN", "").strip()
)
try:
    NARRATO_ENGINE_TIMEOUT = max(2.0, float(os.environ.get("VIDEO_HUB_NARRATO_ENGINE_TIMEOUT", "8")))
except ValueError:
    NARRATO_ENGINE_TIMEOUT = 8.0

# 字幕样式预览（services/subtitle-preview；Nginx 对外是 /preview/）
NARRATO_PREVIEW_URL = os.environ.get("VIDEO_HUB_NARRATO_PREVIEW_URL", "http://127.0.0.1:8502").rstrip("/")

# Agnes 图像/视频模型（OpenAI 兼容媒体接口）
AGNES_MEDIA_BASE_URL = os.environ.get("AGNES_MEDIA_BASE_URL", "https://api.agnes-ai.cn/v1").rstrip("/")
AGNES_IMAGE_MODEL = os.environ.get("AGNES_IMAGE_MODEL", "agnes-image-2.5-flash")
AGNES_VIDEO_MODEL = os.environ.get("AGNES_VIDEO_MODEL", "agnes-video-2.5-flash")
AGNES_VIDEO_QUERY_URL = os.environ.get("AGNES_VIDEO_QUERY_URL", "https://api.agnes-ai.cn/agnesapi").rstrip("/")
# 生成参考图需要给 Agnes 一个公网可访问的 URL
PUBLIC_BASE_URL = os.environ.get("VIDEO_HUB_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def ensure_dirs():
    for d in (DATA_DIR, VIDEOS_DIR, COVERS_DIR, TMP_DIR, RECORDINGS_DIR, COOKIES_DIR):
        d.mkdir(parents=True, exist_ok=True)
