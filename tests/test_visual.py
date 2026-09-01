from datetime import datetime, timezone
import unittest

from ai_growth_copilot.models import Reel, ReelSample, ReportContext, Transcript
from ai_growth_copilot.report import render_report
from ai_growth_copilot.visual import KEYFRAME_RATIOS, VisualError, keyframe_timestamps


class VisualLayerTests(unittest.TestCase):
    def test_fixed_keyframe_ratios(self) -> None:
        self.assertEqual(KEYFRAME_RATIOS, (0.10, 0.50, 0.90))
        self.assertEqual(keyframe_timestamps(100.0), (10.0, 50.0, 90.0))
        with self.assertRaises(VisualError):
            keyframe_timestamps(0)

    def test_video_keyframes_render_separately(self) -> None:
        report = render_report(_context(_sample_with_visual("video_keyframes"), vision_enabled=True))
        self.assertIn("#### 视觉证据", report)
        self.assertIn("- 视觉素材类型：视频关键帧", report)
        self.assertIn("固定取视频时长约 10%、50%、90%", report)
        self.assertIn("- Caption：caption evidence", report)
        self.assertIn("- 转录摘要：transcript evidence", report)
        self.assertIn("画面文字与口播产品名不同", report)

    def test_cover_only_render(self) -> None:
        sample = _sample_with_visual("cover_only")
        report = render_report(_context(sample, vision_enabled=True))
        self.assertIn("- 视觉素材类型：仅封面", report)
        self.assertIn("仅分析封面，未取得视频关键帧", report)

    def test_visual_failure_does_not_hide_text(self) -> None:
        reel = Reel(url="https://www.instagram.com/reel/FAIL/", shortcode="FAIL", caption="caption remains")
        sample = ReelSample(
            reel=reel,
            transcript=Transcript(reel_url=reel.url, text="transcript remains"),
            visual_error="Apify 元数据未提供 videoUrl 或 displayUrl",
        )
        report = render_report(_context(sample, vision_enabled=True))
        self.assertIn("- Caption：caption remains", report)
        self.assertIn("- 转录摘要：transcript evidence", report)
        self.assertIn("- 视觉素材类型：未取得视觉素材", report)
        self.assertIn("Apify 元数据未提供 videoUrl 或 displayUrl", report)
        self.assertIn("- 视觉缺失条数：1", report)


def _sample_with_visual(source_type: str) -> ReelSample:
    reel = Reel(url="https://www.instagram.com/reel/VISUAL/", shortcode="VISUAL", caption="caption evidence")
    note = (
        "视频关键帧：固定取视频时长约 10%、50%、90% 位置"
        if source_type == "video_keyframes"
        else "仅分析封面，未取得视频关键帧"
    )
    return ReelSample(
        reel=reel,
        transcript=Transcript(reel_url=reel.url, text="transcript evidence"),
        visual_evidence={
            "source_type": source_type,
            "source_note": note,
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
            "evidence_cards": [
                {
                    "content_id": content_id,
                    "transcript_summary": "transcript evidence",
                    "cross_modal_conflicts": ["画面文字与口播产品名不同"],
                }
            ]
        },
        analysis_mode="指定 Reel",
        input_urls=[sample.reel.url],
        vision_enabled=vision_enabled,
    )


if __name__ == "__main__":
    unittest.main()
