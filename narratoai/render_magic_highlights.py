import json
import os
import uuid

from app.models.schema import VideoClipParams, VideoAspect
from app.services import task

TASK_ID = "magic-highlights-" + uuid.uuid4().hex[:12]
params = VideoClipParams(
    video_clip_json_path="/opt/NarratoAI/resource/scripts/2026-1028-195233.json",
    video_origin_path="/opt/NarratoAI/resource/videos/magic-performance.mp4",
    video_origin_paths=[],
    video_aspect=VideoAspect.landscape.value,
    voice_name="zh-CN-XiaoyiNeural",
    voice_volume=0.8,
    voice_rate=1.0,
    voice_pitch=1.0,
    tts_engine="edge_tts",
    bgm_name="",
    bgm_type="",
    bgm_file="",
    bgm_volume=0.0,
    sonilo_sfx_enabled=False,
    subtitle_enabled=True,
    subtitle_mask_enabled=False,
    subtitle_auto_transcribe_enabled=False,
    font_name="DroidSansFallbackFull.ttf",
    font_size=60,
    text_fore_color="#FFFFFF",
    stroke_color="#000000",
    stroke_width=1.0,
    subtitle_position="bottom",
    n_threads=2,
    original_volume=1.2,
)
print("TASK_ID=" + TASK_ID, flush=True)
result = task.start_subclip_unified(task_id=TASK_ID, params=params)
print("RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
