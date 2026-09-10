from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from ai_growth_copilot.config import Settings
from ai_growth_copilot.models import Reel, ReportContext, Transcript
from ai_growth_copilot.pipeline import Pipeline, PipelineResult
from ai_growth_copilot.web import (
    WebInputError,
    claim_request,
    execute_web_request,
    parse_report_for_web,
    prepare_web_request,
    release_request,
    request_fingerprint,
    result_issue_lines,
    safe_web_error,
)


class WebInputTests(unittest.TestCase):
    def test_profile_mode_is_normalized(self) -> None:
        request = prepare_web_request(
            "https://instagram.com/lucia.w0o/?utm_source=test",
            "",
            "Lovart, Higgsfield, CapCut",
            True,
        )
        self.assertEqual(request.account_url, "https://www.instagram.com/lucia.w0o/")
        self.assertEqual(request.reel_urls, [])
        self.assertEqual(request.focus_products, ["Lovart", "Higgsfield", "CapCut"])
        self.assertTrue(request.vision)

    def test_three_reels_are_normalized(self) -> None:
        request = prepare_web_request(
            "",
            "\n".join(
                (
                    "https://instagram.com/reels/ONE/?igsh=abc",
                    "https://www.instagram.com/p/TWO/",
                    "https://www.instagram.com/reel/THREE/",
                )
            ),
            "Lovart，Adobe Firefly\nGemini",
            False,
        )
        self.assertEqual(
            request.reel_urls,
            [
                "https://www.instagram.com/reel/ONE/",
                "https://www.instagram.com/p/TWO/",
                "https://www.instagram.com/reel/THREE/",
            ],
        )
        self.assertEqual(request.focus_products, ["Lovart", "Adobe Firefly", "Gemini"])

    def test_profile_and_reels_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(WebInputError, "不可同时"):
            prepare_web_request(
                "https://www.instagram.com/creator/",
                "https://www.instagram.com/reel/ONE/",
                "",
                True,
            )

    def test_missing_too_many_and_duplicate_inputs_are_rejected(self) -> None:
        with self.assertRaisesRegex(WebInputError, "请输入"):
            prepare_web_request("", "", "", True)
        with self.assertRaisesRegex(WebInputError, "最多为 3 条"):
            prepare_web_request(
                "",
                "\n".join(f"https://www.instagram.com/reel/{index}/" for index in range(4)),
                "",
                True,
            )
        with self.assertRaisesRegex(WebInputError, "重复 URL"):
            prepare_web_request(
                "",
                "https://www.instagram.com/reel/ONE/\nhttps://instagram.com/reels/ONE/",
                "",
                True,
            )

    def test_running_lock_blocks_duplicate_submission(self) -> None:
        request = prepare_web_request("https://www.instagram.com/creator/", "", "Lovart", True)
        fingerprint = request_fingerprint(request)
        state: dict[str, object] = {}
        self.assertTrue(claim_request(state, fingerprint))
        self.assertFalse(claim_request(state, fingerprint))
        release_request(state)
        self.assertFalse(state["analysis_running"])

    def test_report_view_keeps_only_decision_body_and_native_appendices(self) -> None:
        report = """# Instagram 创作者内容机制决策报告（V3）

- 分析模式：指定 Reel

## A. 核心结论

- [观察] 结论

## B. 内容机制拆解

| 分析维度 | ONE |
|---|---|
| Hook | 结果先行 |

## C. 对 Lovart 的策略启示

### 可直接采用

- [建议] 采用明确任务

### 优先 A/B 测试

- 测试变量：Hook

## D. 原始证据附录

<details>
<summary>内容 ONE · 原始证据</summary>

- Caption：原始文字

</details>
"""
        view = parse_report_for_web(report)
        self.assertTrue(view.body_markdown.startswith("## A. 核心结论"))
        self.assertIn("## B. 内容机制拆解", view.body_markdown)
        self.assertIn("### 优先 A/B 测试", view.body_markdown)
        self.assertNotIn("分析模式", view.body_markdown)
        self.assertNotIn("## D.", view.body_markdown)
        self.assertEqual(len(view.appendices), 1)
        self.assertEqual(view.appendices[0].title, "内容 ONE · 原始证据")
        self.assertIn("Caption：原始文字", view.appendices[0].markdown)
        self.assertNotIn("<details>", view.appendices[0].markdown)
        self.assertNotIn("<summary>", view.appendices[0].markdown)

    def test_web_app_never_enables_unsafe_html(self) -> None:
        source = Path("web_app.py").read_text(encoding="utf-8")
        self.assertNotIn("unsafe_allow_html=True", source)
        self.assertIn("st.expander(appendix.title, expanded=False)", source)


class WebAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            apify_token="test-apify",
            supadata_api_key="test-supadata",
            deepseek_api_key="test-deepseek",
        )

    def test_adapter_calls_pipeline_directly(self) -> None:
        request = prepare_web_request("https://www.instagram.com/creator/", "", "Lovart", True)
        events: list[tuple[str, str]] = []

        class FakePipeline:
            received: dict[str, object] = {}

            def __init__(self, settings: Settings, output_dir: Path) -> None:
                self.settings = settings
                self.output_dir = output_dir

            def run_with_result(self, **kwargs: object) -> PipelineResult:
                FakePipeline.received = kwargs
                context = ReportContext(
                    account_url=request.account_url,
                    fetched_at=datetime.now(timezone.utc),
                    samples=[],
                    analysis={},
                    vision_enabled=True,
                )
                return PipelineResult(Path("outputs/test.md"), context)

        callback = lambda stage, status: events.append((stage, status))
        result = execute_web_request(
            request,
            self.settings,
            Path("outputs"),
            progress_callback=callback,
            pipeline_class=FakePipeline,  # type: ignore[arg-type]
        )
        self.assertEqual(result.report_path, Path("outputs/test.md"))
        self.assertEqual(FakePipeline.received["account_url"], request.account_url)
        self.assertEqual(FakePipeline.received["progress_callback"], callback)

    def test_missing_keys_fail_before_pipeline_creation(self) -> None:
        request = prepare_web_request("https://www.instagram.com/creator/", "", "", True)
        with self.assertRaisesRegex(WebInputError, "APIFY_TOKEN"):
            execute_web_request(request, Settings("", "", ""), Path("outputs"))

    def test_safe_errors_hide_urls_and_credentials(self) -> None:
        error = WebInputError(
            "download https://media.invalid/video.mp4?token=example-secret key=example-secret"
        )
        message = safe_web_error(error)
        self.assertNotIn("media.invalid", message)
        self.assertNotIn("example-secret", message)

    @patch("ai_growth_copilot.pipeline.write_report", return_value=Path("outputs/report.md"))
    @patch("ai_growth_copilot.pipeline.DeepSeekAnalyzer")
    @patch("ai_growth_copilot.pipeline.DeepSeekVisionAnalyzer")
    @patch("ai_growth_copilot.pipeline.SupadataTranscriber")
    @patch("ai_growth_copilot.pipeline.InstagramScraper")
    def test_pipeline_emits_stage_level_progress_without_external_calls(
        self,
        scraper_class,
        transcriber_class,
        vision_class,
        analyzer_class,
        _write_report,
    ) -> None:
        url = "https://www.instagram.com/reel/ONE/"
        reel = Reel(url=url, shortcode="ONE", raw={"id": "ONE"})
        scraper_class.return_value.fetch_specified_posts.return_value = [reel]
        transcriber_class.return_value.transcribe.return_value = Transcript(url, "transcript")
        vision_class.return_value.analyze_reel.return_value = {"source_type": "video_keyframes"}
        analyzer_class.return_value.analyze.return_value = {}
        events: list[tuple[str, str]] = []

        result = Pipeline(self.settings, Path("outputs")).run_with_result(
            reel_urls=[url],
            vision=True,
            progress_callback=lambda stage, status: events.append((stage, status)),
        )

        self.assertEqual(result.success_count, 1)
        self.assertEqual(
            events,
            [
                ("抓取内容", "running"),
                ("抓取内容", "completed"),
                ("转录", "running"),
                ("转录", "completed"),
                ("视觉处理", "running"),
                ("视觉处理", "completed"),
                ("综合分析", "running"),
                ("综合分析", "completed"),
                ("生成报告", "running"),
                ("生成报告", "completed"),
            ],
        )

    def test_result_issue_lines_do_not_expose_error_urls(self) -> None:
        context = ReportContext(
            account_url="",
            fetched_at=datetime.now(timezone.utc),
            samples=[],
            analysis={},
            analysis_error="request https://api.invalid/path?key=secret failed",
        )
        issues = result_issue_lines(PipelineResult(Path("outputs/test.md"), context))
        self.assertNotIn("api.invalid", " ".join(issues))
        self.assertNotIn("secret", " ".join(issues))


if __name__ == "__main__":
    unittest.main()
