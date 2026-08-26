"""全局配置：所有路径都可通过环境变量覆盖，方便本地与 Docker 部署。"""
import os
import secrets
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
# 扫码登录会话的临时目录（二维码图片、状态文件）
LOGIN_DIR = TMP_DIR / "login"

# 内置发布引擎（social-auto-upload  vendored 副本）
PUBLISHER_DIR = Path(os.environ.get("VIDEO_HUB_PUBLISHER_DIR", BASE_DIR.parent / "publisher")).resolve()
SAU_BIN = Path(os.environ.get("VIDEO_HUB_SAU_BIN", PUBLISHER_DIR / ".venv" / "bin" / "sau"))
SAU_PYTHON = Path(os.environ.get("VIDEO_HUB_SAU_PYTHON", SAU_BIN.parent / "python"))
PUBLISHER_COOKIES_DIR = Path(os.environ.get("VIDEO_HUB_COOKIES_DIR", PUBLISHER_DIR / "cookies")).resolve()

# 默认发布账号名（对应 cookies/<platform>_<account>.json）
PUBLISH_ACCOUNT = os.environ.get("VIDEO_HUB_ACCOUNT", "default")
# 账号名规则（cookie 文件名安全）
ACCOUNT_NAME_RE = r"^[A-Za-z0-9_-]{1,32}$"

# 支持的平台
PLATFORMS = ["douyin", "xiaohongshu", "kuaishou", "bilibili"]
PLATFORM_NAMES = {"douyin": "抖音", "xiaohongshu": "小红书", "kuaishou": "快手", "bilibili": "哔哩哔哩"}
# 网页扫码登录支持的平台（B站走 TV 端官方 API 扫码，见 publisher/qr_login.py）
QR_LOGIN_PLATFORMS = ["douyin", "xiaohongshu", "kuaishou", "bilibili"]
# B站发布默认分区（tid）：174 = 生活记录，可用环境变量覆盖
BILI_TID = int(os.environ.get("VIDEO_HUB_BILI_TID", "174"))

# 无显示环境（服务器）下用 xvfb-run 包裹 sau 命令，抖音校验需要有头浏览器
USE_XVFB = os.environ.get("VIDEO_HUB_XVFB", "").lower() in ("1", "true", "yes") or not os.environ.get("DISPLAY")

# 单个发布任务的超时时间（秒）
PUBLISH_TIMEOUT = int(os.environ.get("VIDEO_HUB_PUBLISH_TIMEOUT", "900"))
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


def _load_sms_token() -> str:
    """短信转发接口的密钥：环境变量优先，否则生成一次并持久化到数据目录。"""
    env = os.environ.get("VIDEO_HUB_SMS_TOKEN")
    if env:
        return env
    f = DATA_DIR / "sms_token.txt"
    if f.exists():
        return f.read_text().strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    f.write_text(token)
    return token


# 手机短信转发 App（SmsForwarder）调用 /api/sms-code 时携带的密钥
SMS_TOKEN = _load_sms_token()


def ensure_dirs():
    for d in (DATA_DIR, VIDEOS_DIR, COVERS_DIR, TMP_DIR, RECORDINGS_DIR, LOGIN_DIR, PUBLISHER_COOKIES_DIR):
        d.mkdir(parents=True, exist_ok=True)
