"""Optional process boundary. The backend calls the same functions in-process by default."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from engine import __version__
from engine.tts import engine_status


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            self._send(200, {"ok": True, "version": __version__, "tts": engine_status()})
            return
        self._send(404, {"ok": False, "error": "未知路径。渲染由 Video Hub 后端进程内调用，不在这里提交任务。"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Video Hub narration engine health server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8601)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"engine health on http://{args.host}:{args.port}/health", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
