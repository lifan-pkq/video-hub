from typing import Any, Callable

from loguru import logger

from app.services.documentary.frame_analysis_service import DocumentaryFrameAnalysisService


class ScriptGenerator:
    def __init__(self, documentary_service: DocumentaryFrameAnalysisService | None = None):
        self.documentary_service = documentary_service or DocumentaryFrameAnalysisService()

    async def generate_script(
        self,
        video_path: str,
        video_theme: str = "",
        custom_prompt: str = "",
        frame_interval_input: int | None = None,
        skip_seconds: int = 0,
        threshold: int = 30,
        vision_batch_size: int | None = None,
        vision_llm_provider: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> list[dict[Any, Any]]:
        callback = progress_callback or (lambda _p, _m: None)
        if skip_seconds != 0 or threshold != 30:
            logger.warning(
                "ScriptGenerator documentary path received "
                f"skip_seconds={skip_seconds} threshold={threshold}; "
                "the shared documentary frame pipeline does not currently apply these parameters."
            )

        return await self.documentary_service.generate_documentary_script(
            video_path=video_path,
            video_theme=video_theme,
            custom_prompt=custom_prompt,
            frame_interval_input=frame_interval_input,
            vision_batch_size=vision_batch_size,
            vision_llm_provider=vision_llm_provider,
            progress_callback=callback,
        )
