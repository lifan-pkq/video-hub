import asyncio
import sys
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services import publisher


class FakeTask:
    def __init__(self):
        self.logs = []
        self.needs_verify = False

    def log(self, message):
        self.logs.append(message)


class HangingProcess:
    def __init__(self):
        self.pid = 4321
        self.returncode = None
        self.killed = False
        self.wait = AsyncMock(return_value=-9)
        self.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

    def kill(self):
        self.killed = True
        self.returncode = -9


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_sau_timeout_starts_when_process_starts(self):
        task = FakeTask()
        with patch("app.services.publisher._build_cmd",
                   return_value=[sys.executable, "-c", "import time; time.sleep(1)"]), \
                patch("app.services.publisher.PUBLISH_TIMEOUT", 0.05):
            started = time.perf_counter()
            code, _ = await publisher._run_sau(task, "douyin", [])
            elapsed = time.perf_counter() - started

        self.assertEqual(code, -1)
        self.assertLess(elapsed, 0.6)
        self.assertTrue(any("执行超时" in line for line in task.logs))

    async def test_check_platform_timeout_kills_and_waits(self):
        proc = HangingProcess()
        with patch("app.services.publisher.sau_available", return_value=True), \
                patch("app.services.publisher._build_cmd", return_value=["sau", "douyin", "check"]), \
                patch("app.services.publisher.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
                patch("os.killpg") as killpg:
            result = await publisher.check_platform("douyin")

        self.assertFalse(result["valid"])
        self.assertEqual(result["detail"], "校验超时")
        killpg.assert_called_once()
        proc.wait.assert_awaited_once()

    async def test_logs_redact_verification_codes_and_url_queries(self):
        class Output:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if lines:
                    return lines.pop(0)
                raise StopAsyncIteration

        lines = [
            "验证码：123456\n".encode(),
            "打开 https://example.test/login?token=secret&code=654321\n".encode(),
        ]
        proc = HangingProcess()
        proc.stdout = Output()
        proc.wait = AsyncMock(return_value=0)
        task = FakeTask()
        with patch("app.services.publisher._build_cmd",
                   return_value=["sau", "douyin", "--url", "https://example.test/start?token=commandsecret"]), \
                patch("app.services.publisher.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
                patch("app.services.publisher.PUBLISH_TIMEOUT", 1):
            _, output = await publisher._run_sau(task, "douyin", [])

        logs = "\n".join(task.logs) + output
        for secret in ["123456", "654321", "token=secret", "token=commandsecret"]:
            self.assertNotIn(secret, logs)
        self.assertIn("https://example.test/login", logs)


if __name__ == "__main__":
    unittest.main()
