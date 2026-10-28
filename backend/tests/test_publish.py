import unittest

from pydantic import ValidationError

from app.routers.publish import PublishRequest


class PublishRequestTests(unittest.TestCase):
    def test_rejects_duplicate_platform_and_account(self):
        target = {"platform": "douyin", "account": "account1"}

        with self.assertRaisesRegex(ValueError, "重复"):
            PublishRequest(video_id=1, title="标题", targets=[target, target])

    def test_accepts_at_most_sixteen_targets(self):
        targets = [{"platform": "douyin", "account": f"account{i}"} for i in range(16)]

        request = PublishRequest(video_id=1, title="标题", targets=targets)
        self.assertEqual(len(request.targets), 16)

        targets.append({"platform": "douyin", "account": "account16"})
        with self.assertRaisesRegex(ValueError, "16"):
            PublishRequest(video_id=1, title="标题", targets=targets)


if __name__ == "__main__":
    unittest.main()
