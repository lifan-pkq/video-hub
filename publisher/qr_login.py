"""扫码登录工作进程：被 video-hub 后端以子进程方式拉起。

引擎（uploader/*）的 *_setup(..., qrcode_callback=...) 会在二维码生成/刷新时回调，
本脚本把最新二维码图片复制到指定路径、并把状态写入 JSON 状态文件；
后端通过状态文件 + 二维码图片向网页前端提供扫码登录能力。

用法:
    python qr_login.py --platform douyin --account myacc \
        --status-file /path/status.json --qr-path /path/qr.png [--headed]
退出码: 0=登录成功, 2=超时或扫码未完成, 1=其他失败
"""
import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conf import BASE_DIR  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--platform", required=True, choices=["douyin", "xiaohongshu", "kuaishou", "bilibili"])
    p.add_argument("--account", required=True)
    p.add_argument("--status-file", required=True, type=Path)
    p.add_argument("--qr-path", required=True, type=Path)
    p.add_argument("--headed", action="store_true", help="有头模式（配合 xvfb 防抖音风控）")
    return p.parse_args()


# ================= B 站扫码登录（TV 端官方 API，无需浏览器） =================
# 与 biliup 登录同一套接口：拿到的 LoginInfo 含真实 token_info（platform=BiliTV），
# 直接落盘 cookies/bilibili_<account>.json，biliup 上传/续期/校验全兼容。

BILI_APPKEY = "4409e2ce8ffd12b8"
BILI_APPSEC = "59b43e04ad6965f34319062b478f83dd"


def _bili_sign(params: dict) -> dict:
    import hashlib

    query = "&".join(f"{k}={v}" for k, v in params.items())
    params["sign"] = hashlib.md5((query + BILI_APPSEC).encode()).hexdigest()
    return params


def bilibili_qr_login(account_file: Path, qr_path: Path, status: "StatusWriter") -> dict:
    import qrcode
    import qrcode.image.svg
    import requests

    http = requests.Session()
    http.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) VideoHub/1.0"

    # 1. 申请二维码
    try:
        r = http.post(
            "https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code",
            data=_bili_sign({"appkey": BILI_APPKEY, "local_id": "0", "ts": int(time.time())}),
            timeout=15,
        ).json()
    except Exception as e:
        return {"success": False, "message": f"网络错误: {e}"}
    if r.get("code") != 0:
        return {"success": False, "message": f"获取二维码失败: {r.get('message') or r}"}
    auth_code = r["data"]["auth_code"]
    qr_url = r["data"]["url"]

    # 2. 生成二维码图片（纯 Python SVG，无需 PIL）
    img = qrcode.make(qr_url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    img.save(str(qr_path))
    status.write("qrcode_ready", "请用哔哩哔哩 App 扫码登录")

    # 3. 轮询扫码状态
    deadline = time.time() + 170
    while time.time() < deadline:
        time.sleep(2)
        try:
            p = http.post(
                "https://passport.bilibili.com/x/passport-tv-login/qrcode/poll",
                data=_bili_sign({"appkey": BILI_APPKEY, "auth_code": auth_code,
                                 "local_id": "0", "ts": int(time.time())}),
                timeout=15,
            ).json()
        except Exception:
            continue
        code = p.get("code")
        if code == 0:
            info = p["data"]
            info["platform"] = "BiliTV"
            account_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"success": True, "message": "登录成功"}
        if code == 86039:  # 二维码已生成，等待扫码/确认
            continue
        return {"success": False, "status": "failed", "message": f"扫码登录失败（code={code}）"}
    return {"success": False, "status": "timeout", "message": "扫码超时，请重新发起"}


class StatusWriter:
    def __init__(self, path: Path):
        self.path = path

    def write(self, state: str, message: str = ""):
        data = {"state": state, "message": message,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def main() -> int:
    args = parse_args()
    status = StatusWriter(args.status_file)
    args.qr_path.parent.mkdir(parents=True, exist_ok=True)
    account_file = Path(BASE_DIR) / "cookies" / f"{args.platform}_{args.account}.json"
    account_file.parent.mkdir(parents=True, exist_ok=True)

    def on_qrcode(payload: dict):
        # 引擎回调：二维码生成或刷新。payload 里带 image_path（可能还有 qrcode_content 等）
        src = payload.get("image_path")
        if src and Path(src).exists():
            shutil.copyfile(src, args.qr_path)
            status.write("qrcode_ready", "请用手机扫码登录")

    async def run() -> dict:
        if args.platform == "bilibili":
            # B站走 TV 端官方 API（同步阻塞），不启动浏览器
            return await asyncio.to_thread(bilibili_qr_login, account_file, args.qr_path, status)
        headless = not args.headed
        # 浏览器实况截图路径：与 status 文件同目录，供 web 端展示页面真实状态
        screenshot_path = str(args.status_file.parent / "screenshot.png")
        if args.platform == "douyin":
            from uploader.douyin_uploader.main import douyin_setup
            return await douyin_setup(str(account_file), handle=True, return_detail=True,
                                      qrcode_callback=on_qrcode, headless=headless,
                                      screenshot_path=screenshot_path)
        if args.platform == "xiaohongshu":
            from uploader.xiaohongshu_uploader.main import xiaohongshu_setup
            return await xiaohongshu_setup(str(account_file), handle=True, return_detail=True,
                                           qrcode_callback=on_qrcode, headless=headless)
        from uploader.ks_uploader.main import ks_setup
        return await ks_setup(str(account_file), handle=True, return_detail=True,
                              qrcode_callback=on_qrcode, headless=headless)

    status.write("starting", "正在打开登录页面…")
    try:
        result = asyncio.run(run())
    except Exception as e:
        status.write("failed", f"登录流程异常: {e}")
        return 1

    if result.get("success"):
        status.write("success", result.get("message") or "登录成功")
        return 0
    state = "timeout" if result.get("status") == "timeout" else "failed"
    status.write(state, result.get("message") or "登录未完成")
    return 2 if state == "timeout" else 1


if __name__ == "__main__":
    sys.exit(main())
