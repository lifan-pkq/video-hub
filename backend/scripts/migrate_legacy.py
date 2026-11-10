"""一次性迁移旧 Narrato 成功成片；不迁移旧任务状态，不删除历史文件。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR, NARRATO_TASKS_DIR  # noqa: E402
from app.services.narrato_import import import_task  # noqa: E402
from app.services.quality import update_quality  # noqa: E402
from app.config import VIDEOS_DIR  # noqa: E402


def main() -> int:
    report = {"started_at": datetime.now().isoformat(timespec="seconds"), "imported": [], "skipped": [], "failed": []}
    for source in sorted(NARRATO_TASKS_DIR.glob("*/combined.mp4")):
        task_dir = source.parent
        try:
            state = json.loads((task_dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if state.get("status") != "success":
            report["skipped"].append({"task_id": task_dir.name, "reason": "旧任务不是成功状态"})
            continue
        try:
            spec = json.loads((task_dir / "spec.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            spec = {}
        try:
            video = import_task(task_dir.name, source, title=spec.get("title"), source_platform="legacy")
            quality = update_quality(video["id"], VIDEOS_DIR / video["filename"])
            report["imported"].append({"task_id": task_dir.name, "video_id": video["id"], "quality_score": quality["score"]})
        except Exception as exc:  # noqa: BLE001
            report["failed"].append({"task_id": task_dir.name, "error": str(exc)})
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = DATA_DIR / "migrations" / "latest-legacy-report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"imported": len(report["imported"]), "skipped": len(report["skipped"]), "failed": len(report["failed"]), "report": str(target)}, ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
