"""Offline tests for the Streamlit presentation layer."""

from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "web_app.py"


class DemoPresentationTests(unittest.TestCase):
    def test_demo_is_default_and_never_calls_pipeline_or_network(self) -> None:
        with (
            patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden")) as network,
            patch("ai_growth_copilot.web.execute_web_request", side_effect=AssertionError("Live forbidden")) as live,
        ):
            app = AppTest.from_file(str(APP)).run(timeout=20)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.radio[0].value, "Demo Mode")
            self.assertIn("虚构示例", app.info[0].value)
            self.assertEqual(len(app.metric), 3)
            self.assertTrue(all("示例" in metric.label for metric in app.metric))
            self.assertTrue(any("项目概览" == heading.value for heading in app.sidebar.subheader))
            self.assertTrue(any("核心结论" in block.value for block in app.markdown))
            self.assertGreaterEqual(len(app.expander), 4)
            self.assertTrue(all(not expander.proto.expanded for expander in app.expander))
            self.assertFalse(any("<details>" in block.value or "<summary>" in block.value for block in app.markdown))
            app.run(timeout=20)
            self.assertEqual(len(app.exception), 0)
            network.assert_not_called()
            live.assert_not_called()


if __name__ == "__main__":
    unittest.main()
