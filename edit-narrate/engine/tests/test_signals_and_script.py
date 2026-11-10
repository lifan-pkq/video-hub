from engine.ass_sub import ass_time, write_ass
from engine.rank import rank
from engine.scripting import normalize_beats, skeleton_beats
from engine.signals import DROP_THRESHOLD, build_suggestions, parse_black, parse_scenes, parse_silence


SILENCE_LOG = """
[silencedetect @ 0x1] silence_start: 4.0
[silencedetect @ 0x1] silence_end: 9.04 | silence_duration: 5.04
"""
BLACK_LOG = "[blackdetect @ 0x1] black_start:6.52 black_end:9.04 black_duration:2.52"
SCENE_LOG = "pts_time:6.52\npts_time:6.60\npts_time:9.04\n"


def test_parsers_and_suggestions():
    silence = parse_silence(SILENCE_LOG, 14)
    assert silence == [{"start": 4.0, "end": 9.04}]
    assert parse_black(BLACK_LOG)[0]["start"] == 6.52
    scenes = parse_scenes(SCENE_LOG)
    assert scenes == [6.52, 9.04]
    suggestions = build_suggestions(3, {
        "duration": 14,
        "silence": silence,
        "black": [{"start": 6.52, "end": 9.04}],
        "scenes": scenes,
        "silence_db": -32,
    }, [
        {"start": 0.3, "end": 3.6, "text": "这是一段演示口播。"},
        {"start": 3.7, "end": 4.2, "text": "嗯"},
        {"start": 9.3, "end": 13.5, "text": "黑场之后继续。"},
    ])
    kinds = {item["kind"] for item in suggestions}
    assert {"silence", "black", "filler", "scene"} <= kinds
    drops = [item for item in suggestions if item["action"] == "drop"]
    assert all(item["end"] > item["start"] for item in drops)
    assert any(item["recommended"] for item in drops if item["score"] >= DROP_THRESHOLD)


def test_signal_plan_does_not_call_model():
    suggestions = build_suggestions(1, {
        "duration": 10,
        "silence": [{"start": 1, "end": 3}],
        "black": [],
        "scenes": [3],
        "silence_db": -32,
    }, [])
    planned = rank(suggestions, instruction="", duration=10, use_llm=False)
    assert planned["source"] == "signal"
    assert planned["suggestions"]
    assert planned["warnings"]


def test_script_normalizes_overlap_and_skeleton():
    beats, warnings = normalize_beats([
        {"id": "b1", "start": 0, "end": 4, "narration": "先说这件事。", "purpose": "hook"},
        {"id": "b2", "start": 3, "end": 8, "narration": "然后补上原因。", "purpose": "explain"},
        {"id": "b3", "start": 8, "end": 8.1, "narration": "太短", "purpose": "close"},
    ], 10)
    assert beats[1]["start"] >= beats[0]["end"]
    assert all(item["id"] != "b3" for item in beats)
    assert warnings
    skeleton, notes = skeleton_beats([
        {"start": 0.2, "end": 2.5, "text": "第一句有内容。"},
        {"start": 2.6, "end": 5.0, "text": "第二句接着说。"},
    ], 8, 8)
    assert skeleton
    assert "没有润色" in notes[0]


def test_ass_timestamp(tmp_path):
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(65.2) == "0:01:05.20"
    path = tmp_path / "out.ass"
    write_ass(path, [{"start": 0, "end": 1.5, "text": "字幕"}], width=960, height=540)
    text = path.read_text(encoding="utf-8")
    assert "PlayResX: 960" in text
    assert "字幕" in text
