#!/usr/bin/env python3
"""一次性/可重复安装 Video Hub 内置人物/场景/风格档案。

用法（在 backend 目录、正确虚拟环境中）:
  python -m scripts.seed_profiles
  # 或
  python scripts/seed_profiles.py

也可调用线上接口:
  curl -X POST http://127.0.0.1:8000/api/profiles/seed
  curl -X POST 'http://127.0.0.1:8000/api/profiles/seed?force=true'
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.profiles import seed_builtins  # noqa: E402


def main() -> int:
    force = "--force" in sys.argv
    result = seed_builtins(force=force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
