from engine.ass_sub import ass_time, write_ass
from engine.highlight import signal_plan
from engine.rank import rank
from engine.scripting import fit_beats_pacing, normalize_beats, skeleton_beats, split_narration
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


def test_pause_beats_are_kept():
    beats, warnings = normalize_beats([
        {"id": "b1", "start": 0, "end": 4, "narration": "先说这件事。", "purpose": "hook"},
        {"id": "b2", "start": 4, "end": 7.5, "narration": "", "purpose": "explain", "speed": 1.5, "freeze": 0.8},
        {"id": "b3", "start": 8, "end": 8.1, "narration": "太短", "purpose": "close"},
    ], 10)
    paused = next(item for item in beats if item["id"] == "b2")
    assert paused["pause"] is True
    assert paused["narration"] == ""
    assert paused["speed"] == 1.5
    assert paused["freeze"] == 0.8
    assert any("暂停解说" in item for item in warnings)
    assert all(item["id"] != "b3" for item in beats)


def test_signal_highlight_keeps_speech_not_waste():
    plan = signal_plan([{
        "video_id": 3,
        "duration": 14,
        "segments": [
            {"start": 0.3, "end": 3.6, "text": "这是一段演示口播，用来说明切点怎么对齐句子。"},
            {"start": 9.3, "end": 13.5, "text": "黑场之后画面切到青绿，句子从这里重新开始。"},
        ],
        "scenes": [6.5, 9.0],
        "silence": [{"start": 4.0, "end": 6.5}],
        "black": [{"start": 6.5, "end": 9.0}],
        "suggestions": [],
    }])
    assert plan["clips"]
    for clip in plan["clips"]:
        assert clip["end"] - clip["start"] >= 1
        assert clip["end"] <= 4.2 or clip["start"] >= 8.8
        assert clip["speed"] == 1
    assert plan["peaks"]


def test_ass_timestamp(tmp_path):
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(65.2) == "0:01:05.20"
    path = tmp_path / "out.ass"
    write_ass(path, [{"start": 0, "end": 1.5, "text": "字幕"}], width=960, height=540)
    text = path.read_text(encoding="utf-8")
    assert "PlayResX: 960" in text
    assert "字幕" in text



def test_split_narration_keeps_full_sentences():
    parts = split_narration("房间里的人已经走到窗边。桌上的图纸被风吹得翻了起来。")
    assert parts == ["房间里的人已经走到窗边。", "桌上的图纸被风吹得翻了起来。"]


def test_fit_beats_pacing_splits_and_adds_freeze():
    raw = [{
        "id": "b1",
        "start": 0,
        "end": 3,
        "narration": "房间里的人已经走到窗边。桌上的图纸被风吹得翻了起来。",
        "purpose": "hook",
    }]
    fitted, notes = fit_beats_pacing(raw, 20)
    assert len(fitted) == 2
    assert any("拆成" in item for item in notes)
    spoken = "".join(item["narration"] for item in fitted)
    assert "走到窗边" in spoken and "图纸" in spoken
    for beat in fitted:
        picture = (beat["end"] - beat["start"]) / float(beat["speed"] or 1) + float(beat["freeze"] or 0)
        assert len(beat["narration"]) <= picture * 4.3 * 1.35
    beats, warnings = normalize_beats(fitted, 20)
    assert not any("渲染时可能被拒绝" in item for item in warnings)
    assert beats[0]["freeze"] > 0 or beats[1]["freeze"] > 0 or (beats[-1]["end"] > 3)


def test_fit_beats_pacing_freezes_long_line_on_short_clip():
    raw = [
        {
            "id": "b1",
            "start": 0,
            "end": 2,
            "narration": "镜头跟着他从走廊走到大厅又停在那张旧桌子前面",
            "purpose": "explain",
        },
        {"id": "b2", "start": 2, "end": 5, "narration": "", "purpose": "explain", "pause": True},
    ]
    fitted, notes = fit_beats_pacing(raw, 10)
    beat = next(item for item in fitted if item["id"] == "b1")
    picture = (beat["end"] - beat["start"]) / float(beat["speed"] or 1) + float(beat["freeze"] or 0)
    assert beat["end"] == 2
    assert beat["freeze"] >= 4
    assert len(beat["narration"]) <= picture * 4.3 * 1.35
    assert any("静止" in item for item in notes)
    _, warnings = normalize_beats(fitted, 10)
    assert not any("渲染时可能被拒绝" in item for item in warnings)
