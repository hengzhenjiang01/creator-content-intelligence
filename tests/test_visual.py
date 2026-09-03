from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from ai_growth_copilot.models import Reel, ReelSample, ReportContext, Transcript
from ai_growth_copilot.report import render_report
from ai_growth_copilot.visual import (
    DeepSeekVisionAnalyzer,
    KEYFRAME_RATIOS,
    VisualError,
    _download_media,
    _extract_keyframes,
    _probe_duration,
    _request_failure_stage,
    _validate_media_file,
    keyframe_timestamps,
)


VISION_RESULT = {
    "visible_scene": "可见场景",
    "visual_hook": "可见 Hook",
    "on_screen_text": [],
    "product_ui_or_brand_evidence": "未确认",
    "before_after_or_result_evidence": "静帧不足",
    "visual_content_delivery": "结果画面",
    "confidence_and_limits": "仅限静帧",
}


class VisualLayerTests(unittest.TestCase):
    def test_fixed_keyframe_ratios(self) -> None:
        self.assertEqual(KEYFRAME_RATIOS, (0.10, 0.50, 0.90))
        self.assertEqual(keyframe_timestamps(100.0), (10.0, 50.0, 90.0))
        with self.assertRaises(VisualError):
            keyframe_timestamps(0)

    @patch.object(DeepSeekVisionAnalyzer, "_analyze_images", return_value=VISION_RESULT.copy())
    @patch("ai_growth_copilot.visual._prepare_video", return_value=[Path("frame.jpg")])
    def test_video_download_path(self, prepare_video: MagicMock, analyze: MagicMock) -> None:
        reel = Reel(url="https://instagram.com/reel/ID/", video_url="https://media.invalid/video?token=secret")
        result = DeepSeekVisionAnalyzer("key", "vision").analyze_reel(reel)
        self.assertEqual(result["source_type"], "video_keyframes")
        self.assertEqual(result["download_object"], "video")
        self.assertEqual(result["frame_ratios"], [0.10, 0.50, 0.90])
        prepare_video.assert_called_once()
        analyze.assert_called_once()

    @patch.object(DeepSeekVisionAnalyzer, "_analyze_images", return_value=VISION_RESULT.copy())
    @patch("ai_growth_copilot.visual._prepare_cover", return_value=[Path("cover.jpg")])
    @patch(
        "ai_growth_copilot.visual._prepare_video",
        side_effect=VisualError("connection", "ConnectionError", download_object="video"),
    )
    def test_video_failure_falls_back_to_cover(
        self,
        prepare_video: MagicMock,
        prepare_cover: MagicMock,
        analyze: MagicMock,
    ) -> None:
        reel = Reel(
            url="https://instagram.com/reel/ID/",
            video_url="https://media.invalid/video?token=secret",
            display_url="https://media.invalid/cover?token=secret",
        )
        result = DeepSeekVisionAnalyzer("key", "vision").analyze_reel(reel)
        self.assertEqual(result["source_type"], "cover_only")
        self.assertEqual(result["source_note"], "仅分析封面：视频关键帧下载失败")
        self.assertEqual(result["download_object"], "cover")
        self.assertEqual(result["video_fallback_failure"]["failure_stage"], "connection")
        self.assertNotIn("media.invalid", str(result))
        prepare_video.assert_called_once()
        prepare_cover.assert_called_once()
        analyze.assert_called_once()

    @patch("ai_growth_copilot.visual._prepare_cover", side_effect=VisualError("HTTP", "HTTPError", download_object="cover", status_code=403))
    @patch("ai_growth_copilot.visual._prepare_video", side_effect=VisualError("DNS", "ConnectionError", download_object="video"))
    def test_video_and_cover_failure_are_both_diagnosed(
        self,
        prepare_video: MagicMock,
        prepare_cover: MagicMock,
    ) -> None:
        reel = Reel(url="https://instagram.com/reel/ID/", video_url="https://secret/video", display_url="https://secret/cover")
        with self.assertRaises(VisualError) as caught:
            DeepSeekVisionAnalyzer("key", "vision").analyze_reel(reel)
        diagnostic = caught.exception.to_dict(reel)
        self.assertEqual(len(diagnostic["failures"]), 2)
        self.assertEqual(diagnostic["failures"][0]["failure_stage"], "DNS")
        self.assertEqual(diagnostic["failures"][1]["status_code"], 403)
        self.assertNotIn("https://", str(diagnostic))
        prepare_video.assert_called_once()
        prepare_cover.assert_called_once()

    @patch.object(DeepSeekVisionAnalyzer, "_analyze_images", return_value=VISION_RESULT.copy())
    @patch("ai_growth_copilot.visual._prepare_cover", return_value=[Path("cover.jpg")])
    def test_missing_video_uses_cover(self, prepare_cover: MagicMock, analyze: MagicMock) -> None:
        reel = Reel(url="https://instagram.com/p/ID/", display_url="https://media.invalid/cover")
        result = DeepSeekVisionAnalyzer("key", "vision").analyze_reel(reel)
        self.assertEqual(result["source_note"], "仅分析封面，未取得视频关键帧")
        self.assertFalse(result["has_video_url"])
        prepare_cover.assert_called_once()
        analyze.assert_called_once()

    def test_request_failure_categories(self) -> None:
        self.assertEqual(_request_failure_stage(requests.ConnectTimeout()), "timeout")
        self.assertEqual(_request_failure_stage(requests.ConnectionError("NameResolutionError")), "DNS")
        self.assertEqual(_request_failure_stage(requests.ConnectionError("connection refused")), "connection")
        response = requests.Response()
        response.status_code = 403
        self.assertEqual(_request_failure_stage(requests.HTTPError(response=response)), "HTTP")

    def test_file_format_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "media.mp4"
            path.write_text("<html>not media</html>", encoding="utf-8")
            with self.assertRaises(VisualError) as caught:
                _validate_media_file(path, "text/html", "video")
            self.assertEqual(caught.exception.stage, "file_format")

    @patch("ai_growth_copilot.visual.shutil.which", return_value=None)
    def test_ffprobe_failure(self, which: MagicMock) -> None:
        with self.assertRaises(VisualError) as caught:
            _probe_duration(Path("video.mp4"))
        self.assertEqual(caught.exception.stage, "ffprobe")
        which.assert_called_once_with("ffprobe")

    @patch("ai_growth_copilot.visual.shutil.which", return_value=None)
    def test_ffmpeg_failure(self, which: MagicMock) -> None:
        with self.assertRaises(VisualError) as caught:
            _extract_keyframes(Path("video.mp4"), 10.0, Path("."))
        self.assertEqual(caught.exception.stage, "ffmpeg")
        which.assert_called_once_with("ffmpeg")

    @patch("ai_growth_copilot.visual.requests.post", side_effect=requests.ConnectTimeout())
    def test_vision_model_failure(self, post: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "frame.jpg"
            image.write_bytes(b"\xff\xd8\xff\xd9")
            with self.assertRaises(VisualError) as caught:
                DeepSeekVisionAnalyzer("key", "vision")._analyze_images([image], "video_keyframes")
        self.assertEqual(caught.exception.stage, "vision_model")
        self.assertEqual(caught.exception.exception_category, "ConnectTimeout")
        post.assert_called_once()

    @patch("ai_growth_copilot.visual._media_session")
    def test_download_uses_headers_timeouts_and_validates_video(self, media_session: MagicMock) -> None:
        session = media_session.return_value.__enter__.return_value
        response = session.get.return_value.__enter__.return_value
        response.headers = {"Content-Type": "video/mp4"}
        response.iter_content.return_value = [b"\x00\x00\x00\x18ftypisom"]
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "video.mp4"
            _download_media("https://media.invalid/private?token=secret", destination, 120, "video")
            self.assertTrue(destination.exists())
        kwargs = session.get.call_args.kwargs
        self.assertIn("User-Agent", kwargs["headers"])
        self.assertEqual(kwargs["timeout"], (10, 120))
        self.assertTrue(kwargs["allow_redirects"])

    def test_video_keyframes_render_separately(self) -> None:
        report = render_report(_context(_sample_with_visual("video_keyframes"), vision_enabled=True))
        self.assertIn("#### 视觉证据", report)
        self.assertIn("- 视觉素材类型：视频关键帧", report)
        self.assertIn("固定取视频时长约 10%、50%、90%", report)
        self.assertIn("- Caption：caption evidence", report)
        self.assertIn("- 口播转录摘要：transcript evidence", report)
        self.assertIn("画面文字与口播产品名不同", report)

    def test_cover_only_render(self) -> None:
        sample = _sample_with_visual("cover_only")
        sample.visual_evidence["video_fallback_failure"] = {
            "failure_stage": "timeout",
            "exception_category": "ReadTimeout",
            "status_code": None,
        }
        report = render_report(_context(sample, vision_enabled=True))
        self.assertIn("- 视觉素材类型：仅封面", report)
        self.assertIn("仅分析封面", report)
        self.assertIn("封面降级原因：视频：请求超时", report)

    def test_vision_disabled_is_rendered_as_one_operational_note(self) -> None:
        url = "https://www.instagram.com/reel/OFF/"
        sample = ReelSample(
            reel=Reel(url=url, shortcode="OFF"),
            transcript=Transcript(reel_url=url, text="text"),
        )
        report = render_report(_context(sample, vision_enabled=False))
        self.assertEqual(report.count("视觉分析：未启用"), 1)
        self.assertNotIn("#### 视觉证据", report)
        self.assertNotIn("视觉分析未启用，本次相关结论未使用视频画面证据。", report)
        self.assertNotIn("videoUrl", report)
        self.assertNotIn("NotRequested", report)

    def test_visual_failure_does_not_hide_text(self) -> None:
        reel = Reel(url="https://www.instagram.com/reel/FAIL/", shortcode="FAIL", caption="caption remains", video_url="secret")
        sample = ReelSample(
            reel=reel,
            transcript=Transcript(reel_url=reel.url, text="transcript remains"),
            visual_error={
                "has_video_url": True,
                "has_cover_url": False,
                "download_object": "video",
                "failure_stage": "DNS",
                "exception_category": "ConnectionError",
                "status_code": None,
                "failures": [],
            },
        )
        report = render_report(_context(sample, vision_enabled=True))
        self.assertIn("- Caption：caption remains", report)
        self.assertIn("- 口播转录摘要：transcript evidence", report)
        self.assertIn("未取得视觉素材（视频：DNS 解析失败）", report)
        self.assertNotIn("videoUrl", report)
        self.assertNotIn("异常类别", report)
        self.assertNotIn("secret", report)
        self.assertIn("视觉缺失 1 条", report)

    def test_both_download_failures_render_without_media_urls(self) -> None:
        reel = Reel(url="https://www.instagram.com/reel/BOTH/", shortcode="BOTH")
        sample = ReelSample(
            reel=reel,
            visual_error={
                "has_video_url": True,
                "has_cover_url": True,
                "download_object": "cover",
                "failure_stage": "HTTP",
                "exception_category": "HTTPError",
                "status_code": 403,
                "failures": [
                    {"download_object": "video", "failure_stage": "DNS", "exception_category": "ConnectionError", "status_code": None},
                    {"download_object": "cover", "failure_stage": "HTTP", "exception_category": "HTTPError", "status_code": 403},
                ],
            },
        )
        report = render_report(_context(sample, vision_enabled=True))
        self.assertIn("视频：DNS 解析失败", report)
        self.assertIn("封面：媒体服务器拒绝请求（HTTP 403）", report)
        self.assertNotIn("异常类别", report)
        self.assertNotIn("token=", report)


def _sample_with_visual(source_type: str) -> ReelSample:
    reel = Reel(url="https://www.instagram.com/reel/VISUAL/", shortcode="VISUAL", caption="caption evidence")
    note = (
        "视频关键帧：固定取视频时长约 10%、50%、90% 位置"
        if source_type == "video_keyframes"
        else "仅分析封面：视频关键帧下载失败"
    )
    return ReelSample(
        reel=reel,
        transcript=Transcript(reel_url=reel.url, text="transcript evidence"),
        visual_evidence={
            "source_type": source_type,
            "source_note": note,
            "has_video_url": True,
            "has_cover_url": True,
            "download_object": "video" if source_type == "video_keyframes" else "cover",
            "visible_scene": "一名创作者面对屏幕",
            "visual_hook": "快速切换画面",
            "on_screen_text": ["Visible text"],
            "product_ui_or_brand_evidence": "可见产品标志",
            "before_after_or_result_evidence": "静帧不足以确认完整前后对比",
            "visual_content_delivery": "展示结果画面",
            "confidence_and_limits": "只观察有限静帧",
        },
    )


def _context(sample: ReelSample, vision_enabled: bool) -> ReportContext:
    content_id = sample.reel.shortcode
    return ReportContext(
        account_url="",
        fetched_at=datetime.now(timezone.utc),
        samples=[sample],
        analysis={
            "evidence_appendix": [
                {
                    "content_id": content_id,
                    "transcript_summary": "transcript evidence",
                    "cross_modal_conflicts": ["画面文字与口播产品名不同"] if sample.visual_evidence else [],
                }
            ]
        },
        analysis_mode="指定 Reel",
        input_urls=[sample.reel.url],
        vision_enabled=vision_enabled,
    )


if __name__ == "__main__":
    unittest.main()
