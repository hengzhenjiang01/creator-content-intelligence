from dataclasses import replace
from datetime import datetime, timezone
import re
import unittest

from ai_growth_copilot.models import Reel, ReelSample, ReportContext, Transcript
from ai_growth_copilot.report import MECHANISM_FIELDS, render_report


class ReportV2Tests(unittest.TestCase):
    def test_v2_has_four_decision_sections_without_legacy_duplication(self) -> None:
        report = render_report(_context())
        headings = [
            "## A. 核心结论",
            "## B. 内容机制拆解",
            "## C. 对 Lovart 的策略启示",
            "## D. 原始证据附录",
        ]
        self.assertEqual([report.index(heading) for heading in headings], sorted(report.index(heading) for heading in headings))
        self.assertNotIn("## 逐条证据卡", report)
        self.assertNotIn("## 内容机制结论", report)
        self.assertNotIn("## 互动数据对比", report)
        self.assertNotIn("第六条不应渲染", report)
        self.assertIn("- 分析范围：该账号的 2 条公开内容；结论不外推至账号整体。", report)
        for _, label in MECHANISM_FIELDS:
            self.assertIn(f"| {label} |", report)
        self.assertIn("| [ONE](https://www.instagram.com/reel/ONE/) |", report)
        before_appendix = report.split("## D. 原始证据附录", 1)[0]
        table = before_appendix.split("## B. 内容机制拆解", 1)[1].split("## C.", 1)[0]
        self.assertNotIn("[观察]", table)
        self.assertNotIn("[推断]", table)
        self.assertEqual(table.count("https://www.instagram.com/reel/ONE/"), 1)

    def test_evidence_sources_and_test_fields_are_kept_separate(self) -> None:
        report = render_report(_context())
        self.assertIn("- Caption：Caption 原文", report)
        self.assertIn("- 口播转录摘要：口播摘要", report)
        self.assertIn("#### 视觉证据", report)
        self.assertIn("- 视觉素材类型：视频关键帧", report)
        self.assertIn("- 测试变量：开场表达", report)
        self.assertIn("- A 版本：先展示问题", report)
        self.assertIn("- B 版本：先展示结果", report)
        self.assertIn("- 观察指标：3 秒留存率、完播率", report)
        self.assertIn("- 证据依据：样本使用了结果先行结构", report)
        self.assertIn("- 当前不确定性：不能确认开场方式会改善留存", report)

    def test_relationship_requires_explicit_disclosure_evidence(self) -> None:
        context = _context()
        context.analysis["evidence_appendix"][0]["product_relationship"] = "creator partnership"
        context.analysis["evidence_appendix"][0]["relationship_evidence"] = ""
        report = render_report(context)
        self.assertIn("- 产品关系：unknown", report)
        self.assertIn("- 关系证据：未发现明确的公开合作披露", report)

    def test_renderer_owns_links_and_strips_model_markdown(self) -> None:
        report = render_report(_context())
        self.assertNotIn("[[", report)
        self.assertNotIn("model.invalid", report)
        links = re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", report)
        self.assertTrue(links)
        self.assertTrue(all(url.startswith("https://www.instagram.com/") for _, url in links))
        self.assertNotRegex(report, r"\[[^\]]*\[[^\]]+\]\([^)]+\)[^\]]*\]\([^)]+\)")

    def test_focus_product_is_not_rendered_without_evidence(self) -> None:
        report = render_report(_context())
        self.assertIn("- 产品提及：CapCut", report)
        self.assertNotIn("- 产品提及：Lovart", report)

    def test_single_sample_broad_interaction_claim_is_guarded(self) -> None:
        context = _context()
        context = replace(context, samples=context.samples[:1])
        context.analysis["lovart_strategy"]["worth_testing"][0]["test_variable"] = "结果先行可能提升互动"
        report = render_report(context)
        self.assertNotIn("结果先行可能提升互动", report)
        self.assertIn("需要通过对照测试评估互动差异", report)

    def test_unknown_date_uses_incomplete_time_wording(self) -> None:
        context = _context()
        first = context.samples[0]
        first = replace(first, reel=replace(first.reel, published_at=None))
        context = replace(context, samples=[first, *context.samples[1:]])
        context.analysis["lovart_strategy"]["should_not_infer"][0]["text"] = (
            "发布时间与曝光窗口不同，不能把互动差异归因于 Hook。"
        )
        report = render_report(context)
        self.assertNotIn("发布时间与曝光窗口不同", report)
        self.assertIn(
            "发布时间信息不完整，且已知样本的曝光窗口可能不同，因此不能直接比较互动表现。",
            report,
        )

    def test_incomplete_historical_data_banner_is_opt_in(self) -> None:
        report = render_report(replace(_context(), historical_test_data_incomplete=True))
        notice = "本报告由不完整的历史测试数据渲染，仅用于验证报告结构，不作为正式分析结果。"
        self.assertIn(notice, report)
        self.assertNotIn(notice, render_report(_context()))


def _context() -> ReportContext:
    samples = [_sample("ONE", 1_000, 80, 12), _sample("TWO", 800, 60, 8)]
    shared_ids = ["ONE", "TWO"]
    core = [
        _claim("观察", "[来源](https://model.invalid/item) 呈现结果先行的教程型内容", ["ONE"]),
        _claim("观察", "产品作为制作步骤中的工具出现", ["ONE"]),
        _claim("推断", "两条样本都围绕降低制作门槛", shared_ids, "样本数量有限"),
        _claim("观察", "没有公开合作披露", shared_ids),
        _claim("观察", "第五条不应渲染", ["ONE"]),
        _claim("观察", "第六条不应渲染", ["ONE"]),
    ]
    fields = {
        key: _claim("观察", text, ["ONE"])
        for (key, _), text in zip(
            MECHANISM_FIELDS,
            (
                "教程演示",
                "把长视频裁切成竖屏短视频",
                "需要快速适配短视频平台的视频运营人员",
                "减少逐镜头重新构图的编辑时间",
                "先展示结果",
                "结果—步骤—CTA",
                "作为实现效果的操作工具",
                "解释操作步骤",
                "展示界面与输出结果",
                "评论关键词获取提示词",
                "可复用的剪辑操作方法",
            ),
        )
    }
    analysis = {
        "core_conclusions": core,
        "content_mechanisms": [{"content_id": "ONE", "fields": fields}],
        "lovart_strategy": {
            "can_borrow": [
                {
                    "type": "建议",
                    "text": "可借鉴结果先行再解释步骤的结构",
                    "evidence_ids": ["ONE"],
                    "uncertainty": "尚未在 Lovart 内容中验证",
                }
            ],
            "should_not_infer": [
                _claim("观察", "不能据此判断开场方式带来更高互动", shared_ids)
            ],
            "worth_testing": [
                {
                    "test_variable": "开场表达",
                    "version_a": "先展示问题",
                    "version_b": "先展示结果",
                    "metric": "3 秒留存率、完播率",
                    "evidence_basis": "样本使用了结果先行结构",
                    "evidence_ids": ["ONE"],
                    "current_uncertainty": "不能确认开场方式会改善留存",
                }
            ],
        },
        "evidence_appendix": [
            {
                "content_id": "ONE",
                "source_url": "https://model.invalid/ignored",
                "mentioned_products": ["CapCut"],
                "transcript_summary": "口播摘要",
                "data_quality_notes": ["转录来自自动识别"],
                "cross_modal_conflicts": [],
                "product_relationship": "unknown",
                "relationship_evidence": "",
            },
            {
                "content_id": "TWO",
                "source_url": "https://model.invalid/ignored-two",
                "mentioned_products": [],
                "transcript_summary": "第二条口播摘要",
                "data_quality_notes": [],
                "cross_modal_conflicts": [],
                "product_relationship": "unknown",
                "relationship_evidence": "",
            },
        ],
    }
    return ReportContext(
        account_url="https://www.instagram.com/example/",
        fetched_at=datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc),
        samples=samples,
        analysis=analysis,
        input_urls=["https://www.instagram.com/example/"],
        vision_enabled=True,
    )


def _sample(content_id: str, views: int, likes: int, comments: int) -> ReelSample:
    url = f"https://www.instagram.com/reel/{content_id}/"
    return ReelSample(
        reel=Reel(
            url=url,
            shortcode=content_id,
            author_username="creator",
            caption="Caption 原文",
            published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            views=views,
            likes=likes,
            comments=comments,
            raw={"id": content_id},
        ),
        transcript=Transcript(reel_url=url, text="完整口播文本", language="en"),
        visual_evidence={
            "source_type": "video_keyframes",
            "source_note": "固定取视频时长约 10%、50%、90% 位置",
            "visible_scene": "创作者展示编辑界面",
            "visual_hook": "首帧展示输出结果",
            "on_screen_text": ["Before", "After"],
            "product_ui_or_brand_evidence": "画面可见 CapCut UI",
            "before_after_or_result_evidence": "可见两张不同结果画面，静帧不足以确认完整过程",
            "visual_content_delivery": "展示最终成片",
            "confidence_and_limits": "仅基于三张关键帧",
        },
    )


def _claim(kind: str, text: str, ids: list[str], uncertainty: str = "") -> dict[str, object]:
    return {"type": kind, "text": text, "evidence_ids": ids, "uncertainty": uncertainty}


if __name__ == "__main__":
    unittest.main()
