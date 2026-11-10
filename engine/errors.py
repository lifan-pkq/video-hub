"""Errors that should reach the UI with a next step, never as an empty success."""


class EngineError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "engine",
        action: str = "",
        status: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.action = action
        self.status = status

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "action": self.action}
