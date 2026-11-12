from engine.highlight import signal_plan
from engine.rank import rank
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
