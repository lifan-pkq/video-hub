from dataclasses import dataclass, field


@dataclass(slots=True)
class DocumentaryAnalysisConfig:
    video_path: str
    frame_interval_seconds: float
    vision_batch_size: int
    vision_llm_provider: str
    vision_model_name: str
    custom_prompt: str = ""
    max_concurrency: int = 2

    def __post_init__(self) -> None:
        if self.frame_interval_seconds <= 0:
            raise ValueError("frame_interval_seconds must be > 0")
        if self.vision_batch_size <= 0:
            raise ValueError("vision_batch_size must be > 0")
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be > 0")


@dataclass(slots=True)
class FrameBatchResult:
    batch_index: int
    status: str
    time_range: str
    raw_response: str
    frame_paths: list[str] = field(default_factory=list)
    frame_observations: list[dict] = field(default_factory=list)
    overall_activity_summary: str = ""
    fallback_summary: str = ""
    error_message: str = ""
