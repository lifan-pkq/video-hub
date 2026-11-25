"""句尾以声音能量为准。文件末尾的空白不能当成还在说话，也不能越过下一句。"""
import array
import math
import unittest
import wave
from pathlib import Path

from app.services.acoustic_tail import apply_acoustic_tails
from app.services.ai_edit import AIResponseError, AIServiceError
from app.services.douyin_plan import plan_douyin_shorts, sentence_end_within


def _tone(rate: int, seconds: float, amp: float) -> array.array:
    data = array.array("h")
    count = int(rate * seconds)
    for index in range(count):
        if amp <= 0:
            data.append(0)
        else:
            data.append(int(amp * math.sin(2 * math.pi * 220 * index / rate)))
    return data


def _write(path: Path, pieces: list[tuple[float, float]]) -> float:
    rate = 16000
    pcm = array.array("h")
    for seconds, amp in pieces:
        pcm.extend(_tone(rate, seconds, amp))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return len(pcm) / rate


def _rms_after(path: Path, start: float, end: float) -> float:
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        handle.setpos(int(start * rate))
        raw = handle.readframes(max(1, int((end - start) * rate)))
    pcm = array.array("h")
    pcm.frombytes(raw[: len(raw) // 2 * 2])
    if not pcm:
        return 0.0
    return (sum(sample * sample for sample in pcm) / len(pcm)) ** 0.5


class AcousticTailTests(unittest.TestCase):
    def test_decay_is_kept_and_trailing_silence_is_not(self):
        path = Path(self.id().replace(".", "_") + ".wav")
        self.addCleanup(path.unlink, missing_ok=True)
        duration = _write(path, [(0.80, 8000), (0.25, 2500), (1.2, 0), (0.4, 8000)])
        segments = [
            {"start": 0.0, "end": 0.55, "text": "上一句说完了"},
            {"start": 2.25, "end": 2.6, "text": "下一句开始"},
        ]
        refined = apply_acoustic_tails(path, segments, duration)
        end = refined[0]["end"]
        self.assertGreater(end, 1.0)
        self.assertLess(end, 1.45)
        self.assertLess(end, refined[1]["start"])
        self.assertLess(_rms_after(path, end, 2.1), 40)
        self.assertGreater(_rms_after(path, 0.9, 1.05), 40)

    def test_continuous_speech_stops_at_the_next_sentence(self):
        path = Path(self.id().replace(".", "_") + ".wav")
        self.addCleanup(path.unlink, missing_ok=True)
        duration = _write(path, [(2.0, 8000)])
        segments = [
            {"start": 0.0, "end": 0.7, "text": "前一句没有空隙"},
            {"start": 1.0, "end": 1.8, "text": "后一句紧紧挨着"},
        ]
        refined = apply_acoustic_tails(path, segments, duration)
        self.assertLessEqual(refined[0]["end"], 1.0)
        self.assertGreaterEqual(refined[0]["end"], 0.7)
        self.assertGreater(refined[1]["end"], 1.8)
        self.assertLessEqual(refined[1]["end"], duration)

    def test_cap_drops_back_when_the_tail_crosses_the_limit(self):
        path = Path(self.id().replace(".", "_") + ".wav")
        self.addCleanup(path.unlink, missing_ok=True)
        # 第二句的识别时间在 9.7 秒，声音一直到 10.2 秒，上限 10 秒。
        duration = _write(path, [(3.4, 8000), (0.4, 0), (6.2, 8000), (0.4, 0)])
        segments = [
            {"start": 0.0, "end": 3.1, "text": "第一句可以完整留下"},
            {"start": 3.8, "end": 9.7, "text": "第二句的尾音放不进上限"},
        ]
        refined = apply_acoustic_tails(path, segments, duration)
        self.assertGreater(refined[1]["end"], 10.0)
        chosen = sentence_end_within(0.0, refined, 10.0)
        self.assertIsNotNone(chosen)
        self.assertLessEqual(chosen, 10.0)
        self.assertAlmostEqual(chosen, refined[0]["end"], places=2)
        with self.assertRaises(AIResponseError):
            plan_douyin_shorts(
                [{"start": 0.0, "end": refined[1]["end"], "text": "只有一句并且尾音超过十秒上限"}],
                duration,
                max_seconds=10,
                request_ai=lambda *_args: (_ for _ in ()).throw(AIServiceError("文本模型没有配置")),
            )

    def test_old_plan_without_audio_still_uses_the_given_timestamp(self):
        segments = [
            {"start": 0.0, "end": 4.0, "text": "这是一句可以单独听懂的口播"},
            {"start": 4.4, "end": 8.0, "text": "这是下一句可以单独听懂的口播"},
        ]
        end = sentence_end_within(0.0, segments, 6.0)
        self.assertEqual(end, 4.0)


if __name__ == "__main__":
    unittest.main()
