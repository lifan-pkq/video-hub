import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import runner
from engine.store import TaskStore


class RunnerFusionTests(unittest.TestCase):
    @patch("engine.runner.write_understanding", return_value="融合时间线")
    @patch("engine.runner.build_understanding")
    @patch("engine.runner._visual_markdown")
    @patch("engine.runner._asr_markdown")
    def test_auto_uses_transcript_and_visual_together(self, asr, visual, build, write):
        asr.return_value = "口播" * 100
        visual.return_value = "画面"
        build.return_value = {"tracks": {"speech": [{}], "visual": [{}]}}
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(temp)
            task_id = "narrate-fused"
            store.create({"task_id": task_id})
            spec = {"task_id": task_id, "mode": "auto", "video_path": "clip.mp4"}
            result = runner._material_markdown(spec, store)
            state = store.read_state(task_id)
        self.assertEqual(result, "融合时间线")
        asr.assert_called_once()
        visual.assert_called_once()
        self.assertEqual(state["material_source"], "fused")


if __name__ == "__main__":
    unittest.main()
