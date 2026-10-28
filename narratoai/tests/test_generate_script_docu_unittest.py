import unittest

from webui.tools.generate_script_docu import _normalize_progress_value


class GenerateScriptDocuProgressTests(unittest.TestCase):
    def test_normalize_progress_rounds_percentage_float_to_valid_streamlit_int(self):
        self.assertEqual(43, _normalize_progress_value(43.125))

    def test_normalize_progress_converts_ratio_float_to_percentage_int(self):
        self.assertEqual(43, _normalize_progress_value(0.43125))

    def test_normalize_progress_clamps_out_of_range_values(self):
        self.assertEqual(0, _normalize_progress_value(-5))
        self.assertEqual(100, _normalize_progress_value(101))


if __name__ == "__main__":
    unittest.main()
