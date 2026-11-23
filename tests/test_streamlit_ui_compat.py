"""Lightweight source guard against removed Streamlit widget parameters."""
from pathlib import Path
import ast
import unittest


class TestStreamlitUiCompatibility(unittest.TestCase):
    def test_deprecated_width_parameter_not_present(self):
        app = Path(__file__).resolve().parents[1] / "app.py"
        tree = ast.parse(app.read_text(encoding="utf-8"))
        deprecated = "use_container" + "_width"
        bad = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.keyword) and n.arg == deprecated]
        self.assertFalse(bad, f"Deprecated Streamlit widget argument at lines: {bad}")

    def test_legacy_full_width_maps_to_stretch(self):
        app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn('width="stretch"', app)
        self.assertIn('width="content"', app)

    def test_option_scan_outcome_and_http_status_both_visible(self):
        app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn("st.caption(st.session_state.get('option_scan_note'", app)
        self.assertIn("http_note = option_http_note(health)", app)


if __name__ == "__main__":
    unittest.main()
