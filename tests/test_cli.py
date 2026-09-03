import contextlib
from datetime import datetime, timezone
import io
import unittest

from ai_growth_copilot.cli import main, normalize_instagram_content_url
from ai_growth_copilot.models import Reel, ReelSample, ReportContext
from ai_growth_copilot.report import render_report


class CliInputModeTests(unittest.TestCase):
    def test_single_reel_dry_run(self) -> None:
        code, stdout, stderr = _run_main(["--reel", "https://instagram.com/reels/ABC123/?utm_source=test"])
        self.assertEqual(code, 0)
        self.assertIn("分析模式：指定 Reel", stdout)
        self.assertIn("https://www.instagram.com/reel/ABC123/", stdout)
        self.assertEqual(stderr, "")

    def test_three_reels_dry_run(self) -> None:
        code, stdout, _ = _run_main(
            [
                "--reel", "https://www.instagram.com/reel/ONE/",
                "--reel", "https://www.instagram.com/p/TWO/",
                "--reel", "https://www.instagram.com/reels/THREE/",
                "--focus-products", "Lovart", "Higgsfield", "CapCut",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stdout.count("指定输入 "), 3)
        self.assertIn("重点产品：Lovart, Higgsfield, CapCut", stdout)

    def test_profile_and_reel_are_mutually_exclusive(self) -> None:
        code, _, stderr = _run_main(
            ["https://www.instagram.com/creator/", "--reel", "https://www.instagram.com/reel/ONE/"]
        )
        self.assertEqual(code, 2)
        self.assertIn("不可同时使用", stderr)

    def test_missing_input_is_an_error(self) -> None:
        code, _, stderr = _run_main([])
        self.assertEqual(code, 2)
        self.assertIn("必须提供创作者主页 URL", stderr)

    def test_content_url_normalization(self) -> None:
        self.assertEqual(
            normalize_instagram_content_url("https://instagram.com/reels/XYZ/?igsh=abc"),
            "https://www.instagram.com/reel/XYZ/",
        )

    def test_specified_report_header_and_unknown_author(self) -> None:
        url = "https://www.instagram.com/reel/ABC123/"
        report = render_report(
            ReportContext(
                account_url="",
                fetched_at=datetime.now(timezone.utc),
                samples=[ReelSample(reel=Reel(url=url, shortcode="ABC123", raw={"url": url}))],
                analysis={},
                analysis_mode="指定 Reel",
                input_urls=[url],
            )
        )
        self.assertIn("- 分析模式：指定 Reel", report)
        self.assertIn("- 输入链接：", report)
        self.assertIn("- 成功获取：", report)
        self.assertIn("- 作者：作者未知", report)
        self.assertIn("## A. 核心结论", report)
        self.assertIn("## D. 原始证据附录", report)

    def test_vision_dry_run_does_not_execute(self) -> None:
        code, stdout, stderr = _run_main(
            ["--reel", "https://www.instagram.com/reel/VISION/", "--vision"]
        )
        self.assertEqual(code, 0)
        self.assertIn("视觉分析：已请求（dry-run 不下载、不调用视觉模型）", stdout)
        self.assertIn("固定 10%/50%/90% 关键帧", stdout)
        self.assertEqual(stderr, "")


def _run_main(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = int(exc.code)
    return code, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
