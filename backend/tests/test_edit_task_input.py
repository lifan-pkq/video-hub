"""旧的内存剪辑任务入参测试已由信号管线测试取代。"""

import unittest

from app.routers import edit


class EditRouteSurfaceTests(unittest.TestCase):
    def test_signal_routes_exist(self):
        paths = {route.path for route in edit.router.routes}
        self.assertIn("/api/edit/analyze", paths)
        self.assertIn("/api/edit/plan", paths)
        self.assertIn("/api/edit/export", paths)
        self.assertIn("/api/edit/concat", paths)
