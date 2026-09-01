from pathlib import Path
import re
from typing import Any

from .models import ReelSample, ReportContext


FORBIDDEN_SMALL_SAMPLE_PHRASES = {
    "证明": "与……一致，但不能单独确认",
    "说明": "呈现出",
    "高传播力要素": "值得进一步测试的内容要素",
    "主力钩子": "待测试钩子",
    "表现最佳内容": "本次样本中互动量最高的内容",
}
RELATIONSHIPS = {"official", "creator partnership", "organic mention", "comparison", "unknown"}


def write_report(context: ReportContext, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    username = "specified_reels" if context.analysis_mode == "指定 Reel" else _username_from_url(context.account_url)
    stamp = context.fetched_at.strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"{username}_{stamp}.md"
    path.write_text(render_report(context), encoding="utf-8")
    return path


def render_report(context: ReportContext) -> str:
    evidence = _evidence_by_id(context.analysis)
    lines = [
        "# Instagram 创作者内容机制分析报告",
        "",
        f"- 分析模式：{context.analysis_mode}",
        f"- 抓取时间：{context.fetched_at.isoformat()}",
        f"- 样本数：{context.sample_count}",
        f"- 失败条数：{context.failure_count}",
        f"- 视觉分析：{'启用' if context.vision_enabled else '未启用'}",
        f"- 视觉缺失条数：{context.visual_failure_count}",
        "- 分析单位：单条公开内容，不代表账号整体",
        "",
        "## 输入与抓取结果",
        "",
    ]
    if context.account_url:
        lines.append(f"- 分析账号 URL：{context.account_url}")
    lines.append("- 用户提供的输入链接：")
    lines.extend(f"  - {url}" for url in context.input_urls)
    lines.append("- 成功抓取链接：")
    fetched_samples = [
        sample
        for sample in context.samples
        if context.analysis_mode != "指定 Reel" or bool(sample.reel.raw)
    ]
    if fetched_samples:
        lines.extend(f"  - {_link(_content_id(sample), sample.reel.url)}" for sample in fetched_samples)
    else:
        lines.append("  - 无")
    lines.extend([
        "",
        "## 逐条证据卡",
        "",
    ])
    for sample in context.samples:
        lines.extend(_render_evidence_card(sample, evidence.get(_content_id(sample), {})))
    lines.extend(["## 内容机制结论", ""])
    if context.analysis_error:
        lines.extend([f"> 分析未完成：{_plain(context.analysis_error)}", ""])
    for section in _list(context.analysis.get("sections")):
        if not isinstance(section, dict):
            continue
        lines.extend([f"### {_plain(section.get('title') or '分析')}", ""])
        for claim in _list(section.get("claims")):
            rendered = _render_claim(claim, context)
            if rendered:
                lines.append(rendered)
        lines.append("")
    lines.extend(_render_interactions(context))
    lines.extend(["## 风险与样本局限", ""])
    risks = _list(context.analysis.get("risks"))
    if not risks:
        lines.append("- [观察] 当前没有可渲染的模型风险条目；报告仍受公开数据完整性与转录准确性限制。")
    for risk in risks:
        rendered = _render_claim(risk, context)
        if rendered:
            lines.append(rendered)
    lines.extend(["", "## 行动建议", ""])
    recommendations = _list(context.analysis.get("recommendations"))
    if not recommendations:
        lines.append("暂无可渲染的建议。")
    for index, recommendation in enumerate(recommendations, start=1):
        if isinstance(recommendation, dict):
            lines.extend(_render_recommendation(index, recommendation, context))
    return "\n".join(lines).rstrip() + "\n"


def _render_evidence_card(sample: ReelSample, card: dict[str, Any]) -> list[str]:
    reel = sample.reel
    content_id = _content_id(sample)
    relationship = str(card.get("product_relationship") or sample.product_relationship or "unknown")
    if relationship not in RELATIONSHIPS:
        relationship = "unknown"
    quality_notes = [_plain(note) for note in _list(card.get("data_quality_notes")) if _plain(note)]
    if sample.error:
        quality_notes.append(f"处理失败：{_plain(sample.error)}")
    if not reel.published_at:
        quality_notes.append("发布时间缺失")
    if not sample.transcript:
        quality_notes.append("无可用转录")
    lines = [
        f"### {_link(content_id, reel.url)}",
        "",
        f"- Reel URL：{reel.url}",
        f"- 作者账号：{'@' + reel.author_username if reel.author_username else '作者未知'}",
        f"- 发布时间：{reel.published_at.isoformat() if reel.published_at else '未知'}",
        f"- Caption：{_plain(reel.caption) or '未提供'}",
        f"- 实际提及产品：{_render_products(card.get('mentioned_products'))}",
        f"- Hook：{_plain(card.get('hook')) or '未识别到明确 Hook'}",
        f"- 内容交付物：{_plain(card.get('content_deliverable')) or '未识别到明确交付物'}",
        f"- CTA 原文：{_plain(card.get('cta_original')) or '未识别到明确 CTA'}",
        f"- 互动数据：观看 {_number(reel.views)}；点赞 {_number(reel.likes)}；评论 {_number(reel.comments)}",
        f"- 转录摘要：{_plain(card.get('transcript_summary')) or _fallback_summary(sample)}",
        f"- 产品关系：{relationship}",
        f"- 数据质量提示：{'；'.join(quality_notes) if quality_notes else '未发现明确异常；仍需结合原内容复核'}",
        "",
    ]
    lines.extend(_render_visual_evidence(sample, card))
    return lines


def _render_visual_evidence(sample: ReelSample, card: dict[str, Any]) -> list[str]:
    evidence = sample.visual_evidence or {}
    lines = ["#### 视觉证据", ""]
    if not evidence:
        reason = sample.visual_error or "未启用 --vision"
        lines.extend(
            [
                "- 视觉素材类型：未取得视觉素材",
                f"- 视觉缺失原因：{_plain(reason)}",
                "- 跨模态矛盾：无法比较 caption、口播转录与视觉证据",
                "",
            ]
        )
        return lines
    source_type = str(evidence.get("source_type") or "")
    type_label = "视频关键帧" if source_type == "video_keyframes" else "仅封面" if source_type == "cover_only" else "未取得视觉素材"
    lines.extend(
        [
            f"- 视觉素材类型：{type_label}",
            f"- 取帧规则：{_plain(evidence.get('source_note')) or '未知'}",
            f"- 可见场景：{_visual_value(evidence.get('visible_scene'))}",
            f"- 视觉 Hook：{_visual_value(evidence.get('visual_hook'))}",
            f"- 屏幕文字：{_visual_value(evidence.get('on_screen_text'))}",
            f"- 产品 UI/品牌证据：{_visual_value(evidence.get('product_ui_or_brand_evidence'))}",
            f"- 前后对比或结果证据：{_visual_value(evidence.get('before_after_or_result_evidence'))}",
            f"- 视觉内容交付：{_visual_value(evidence.get('visual_content_delivery'))}",
            f"- 置信度与限制：{_visual_value(evidence.get('confidence_and_limits'))}",
        ]
    )
    conflicts = [_plain(item) for item in _list(card.get("cross_modal_conflicts")) if _plain(item)]
    lines.append(
        f"- 跨模态矛盾：{'；'.join(conflicts) if conflicts else '未识别到明确矛盾；不代表三种证据完全一致'}"
    )
    lines.append("")
    return lines


def _render_interactions(context: ReportContext) -> list[str]:
    lines = ["## 互动数据对比", ""]
    comparisons = _list(context.analysis.get("interaction_comparisons"))
    if not comparisons:
        lines.append("暂无可渲染的互动比较。")
        lines.append("")
        return lines
    for comparison in comparisons:
        if not isinstance(comparison, dict):
            continue
        ids = _valid_ids(comparison.get("evidence_ids"), context)
        if len(ids) < 2:
            continue
        for content_id in ids:
            sample = _sample_map(context).get(content_id)
            if sample:
                reel = sample.reel
                lines.append(
                    f"- {_link(content_id, reel.url)}：发布日期 "
                    f"{reel.published_at.date().isoformat() if reel.published_at else '未知'}；"
                    f"观看 {_number(reel.views)}；点赞 {_number(reel.likes)}；评论 {_number(reel.comments)}"
                )
        text = _guard_wording(_plain(comparison.get("text")), context.sample_count)
        uncertainty = _plain(comparison.get("uncertainty"))
        mandatory = "发布时间不同、样本量小，因此不能将表现差异归因于单一内容变量。"
        lines.append(f"- {_claim_label(comparison.get('type'))} {text} {_links(ids, context)}")
        lines.append(f"- [推断] 不确定性：{uncertainty or mandatory} {mandatory if mandatory not in uncertainty else ''}".rstrip())
        lines.append("")
    return lines


def _render_claim(claim: Any, context: ReportContext) -> str:
    if not isinstance(claim, dict):
        return ""
    claim_type = str(claim.get("type") or "观察")
    ids = _valid_ids(claim.get("evidence_ids"), context)
    if claim_type == "推断" and len(ids) < 2:
        return ""
    text = _guard_wording(_plain(claim.get("text")), context.sample_count)
    if not text:
        return ""
    uncertainty = _plain(claim.get("uncertainty"))
    if claim_type == "推断":
        text = text.rstrip("。；; ")
        suffix = f"；不确定性：{uncertainty or '该解释仍需更多样本验证'}"
    else:
        suffix = ""
    return f"- {_claim_label(claim_type)} {text}{suffix} {_links(ids, context)}".rstrip()


def _render_recommendation(index: int, item: dict[str, Any], context: ReportContext) -> list[str]:
    ids = _valid_ids(item.get("evidence_ids"), context)
    return [
        f"### {index}. [建议] {_guard_wording(_plain(item.get('hypothesis')), context.sample_count) or '待补充假设'}",
        "",
        f"- 待测试假设：{_guard_wording(_plain(item.get('hypothesis')), context.sample_count) or '待补充'}",
        f"- 建议内容形式：{_plain(item.get('content_format')) or '待补充'}",
        f"- 为什么值得测试（引用样本）：{_guard_wording(_plain(item.get('why_test')), context.sample_count) or '待补充'} {_links(ids, context)}".rstrip(),
        f"- 不能从当前样本确认什么：{_plain(item.get('cannot_confirm')) or '不能确认该假设会带来更高互动或转化'}",
        "",
    ]


def _evidence_by_id(analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for card in _list(analysis.get("evidence_cards")):
        if isinstance(card, dict) and card.get("content_id"):
            result[str(card["content_id"])] = card
    return result


def _sample_map(context: ReportContext) -> dict[str, ReelSample]:
    return {_content_id(sample): sample for sample in context.samples}


def _valid_ids(value: Any, context: ReportContext) -> list[str]:
    known = _sample_map(context)
    return [str(item) for item in _list(value) if str(item) in known]


def _links(ids: list[str], context: ReportContext) -> str:
    samples = _sample_map(context)
    return "、".join(_link(content_id, samples[content_id].reel.url) for content_id in ids)


def _link(content_id: str, url: str) -> str:
    return f"[{_plain(content_id)}]({_plain(url)})"


def _claim_label(value: Any) -> str:
    return "[推断]" if str(value) == "推断" else "[观察]"


def _content_id(sample: ReelSample) -> str:
    return sample.reel.shortcode or sample.reel.url.rstrip("/").split("/")[-1]


def _fallback_summary(sample: ReelSample) -> str:
    if not sample.transcript:
        return "无可用转录"
    text = " ".join(sample.transcript.text.split())
    return text[:180] + ("…" if len(text) > 180 else "")


def _guard_wording(text: str, sample_count: int) -> str:
    if sample_count >= 5:
        return text
    for forbidden, replacement in FORBIDDEN_SMALL_SAMPLE_PHRASES.items():
        text = text.replace(forbidden, replacement)
    return text


def _plain(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1（\2）", text)
    return text.replace("\n", " ")


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: int | None) -> str:
    return f"{value:,}" if value is not None else "未知"


def _render_products(value: Any) -> str:
    products = [_plain(product) for product in _list(value) if _plain(product)]
    return "、".join(products) if products else "未识别到明确产品提及"


def _visual_value(value: Any) -> str:
    if isinstance(value, list):
        items = [_plain(item) for item in value if _plain(item)]
        return "；".join(items) if items else "未观察到"
    return _plain(value) or "未观察到"


def _username_from_url(url: str) -> str:
    match = re.search(r"instagram\.com/([^/?#]+)", url, flags=re.IGNORECASE)
    username = match.group(1) if match else "instagram"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", username)
