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
MECHANISM_FIELDS = (
    ("content_type", "内容类型"),
    ("hook_type", "Hook"),
    ("narrative_structure", "叙事结构"),
    ("product_role", "产品作用"),
    ("cta", "CTA"),
)


def write_report(context: ReportContext, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    username = "specified_reels" if context.analysis_mode == "指定 Reel" else _username_from_url(context.account_url)
    stamp = context.fetched_at.strftime("%Y%m%d_%H%M%S")
    version = re.sub(r"[^A-Za-z0-9]+", "", context.report_version).lower()
    suffix = f"_{version}" if version else ""
    path = output_dir / f"{username}{suffix}_{stamp}.md"
    path.write_text(render_report(context), encoding="utf-8")
    return path


def render_report(context: ReportContext) -> str:
    appendix = _appendix_by_id(context.analysis)
    lines = [
        f"# Instagram 创作者内容机制决策报告（{_plain(context.report_version)}）",
        "",
    ]
    if context.historical_test_data_incomplete:
        lines.extend(
            [
                "> 本报告由不完整的历史测试数据渲染，仅用于验证报告结构，不作为正式分析结果。",
                "",
            ]
        )
    lines.extend([
        f"- 分析模式：{_plain(context.analysis_mode)}",
        f"- 抓取时间：{context.fetched_at.isoformat()}",
        f"- 分析范围：{_scope_text(context)}",
        f"- 处理失败：{context.failure_count} 条",
        f"- 视觉分析：{'启用' if context.vision_enabled else '未启用'}",
    ])
    if context.account_url:
        lines.append(f"- 分析账号：{_link('Instagram 主页', context.account_url)}")
    if context.input_urls:
        lines.append(f"- 输入链接：{'、'.join(_link(_id_from_url(url), url) for url in context.input_urls)}")
    fetched = [sample for sample in context.samples if context.analysis_mode != "指定 Reel" or bool(sample.reel.raw)]
    lines.append(
        f"- 成功获取：{'、'.join(_link(_content_id(sample), sample.reel.url) for sample in fetched) if fetched else '无'}"
    )

    lines.extend(["", "## A. 核心结论", ""])
    if context.analysis_error:
        lines.extend([f"> 分析未完成：{_plain(context.analysis_error)}", ""])
    conclusions = _list(context.analysis.get("core_conclusions"))[:3]
    rendered_conclusions = [_render_claim(item, context) for item in conclusions]
    rendered_conclusions = [item for item in rendered_conclusions if item]
    lines.extend(rendered_conclusions)

    lines.extend(["", "## B. 内容机制拆解", ""])
    lines.extend(_render_mechanism_matrix(context))

    strategy = context.analysis.get("lovart_strategy")
    strategy = strategy if isinstance(strategy, dict) else {}
    lines.extend(["## C. 对 Lovart 的策略启示", "", "### 可直接采用", ""])
    borrowed = [
        _render_strategy_item(item, context, force_suggestion=True)
        for item in _list(strategy.get("can_borrow"))[:2]
    ]
    borrowed = [item for item in borrowed if item]
    lines.extend(borrowed)

    lines.extend(["", "### 优先 A/B 测试", ""])
    tests = [item for item in _list(strategy.get("worth_testing")) if isinstance(item, dict)]
    if tests:
        lines.extend(_render_test(tests[0], context))

    lines.extend(["", "## D. 原始证据附录", ""])
    for sample in context.samples:
        lines.extend(_render_appendix_item(sample, appendix.get(_content_id(sample), {}), context))
    return "\n".join(lines).rstrip() + "\n"


def _render_appendix_item(sample: ReelSample, item: dict[str, Any], context: ReportContext) -> list[str]:
    reel = sample.reel
    content_id = _content_id(sample)
    notes = [_plain(note) for note in _list(item.get("data_quality_notes")) if _plain(note)]
    if sample.error:
        notes.append(f"处理失败：{_plain(sample.error)}")
    conflicts = [_plain(value) for value in _list(item.get("cross_modal_conflicts")) if _plain(value)]
    lines = [
        "<details>",
        f"<summary>内容 {_plain(content_id)} · 原始证据</summary>",
        "",
        f"- 原始链接：{_link(content_id, reel.url)}",
    ]
    if reel.author_username:
        lines.append(f"- 作者：@{_plain(reel.author_username)}")
    if reel.published_at:
        lines.append(f"- 发布时间：{reel.published_at.date().isoformat()}")
    if reel.caption:
        lines.append(f"- Caption：{_plain(reel.caption)}")
    transcript_summary = _plain(item.get("transcript_summary")) or _fallback_summary(sample)
    if transcript_summary:
        lines.append(f"- 口播转录摘要：{transcript_summary}")
    lines.append("")
    if context.vision_enabled and sample.visual_evidence:
        lines.extend(_render_visual_evidence(sample))
    if conflicts:
        lines.append(f"- 跨模态矛盾：{'；'.join(conflicts)}")
    if notes:
        lines.append(f"- 数据质量：{'；'.join(_deduplicate(notes))}")
    lines.extend(["", "</details>", ""])
    return lines


def _render_visual_evidence(sample: ReelSample) -> list[str]:
    evidence = sample.visual_evidence or {}
    if not evidence:
        return []
    lines = ["#### 视觉证据", ""]
    source_type = str(evidence.get("source_type") or "")
    type_label = "视频关键帧" if source_type == "video_keyframes" else "仅封面" if source_type == "cover_only" else ""
    visual_fields = (
        ("视觉素材类型", type_label),
        ("取样说明", _plain(evidence.get("source_note"))),
        ("可见场景", _visual_value(evidence.get("visible_scene"))),
        ("视觉 Hook", _visual_value(evidence.get("visual_hook"))),
        ("屏幕文字", _visual_value(evidence.get("on_screen_text"))),
        ("产品 UI/品牌证据", _visual_value(evidence.get("product_ui_or_brand_evidence"))),
        ("前后对比或结果证据", _visual_value(evidence.get("before_after_or_result_evidence"))),
        ("视觉内容交付", _visual_value(evidence.get("visual_content_delivery"))),
    )
    lines.extend(f"- {label}：{value}" for label, value in visual_fields if value)
    lines.append("")
    return lines


def _render_mechanism_matrix(context: ReportContext) -> list[str]:
    if not context.samples:
        return ["暂无可渲染的内容机制。", ""]
    mechanisms = _mechanisms_by_id(context.analysis)
    headers = ["分析维度"] + [
        _link(_content_id(sample), sample.reel.url) for sample in context.samples
    ]
    lines = [
        "| " + " | ".join(_cell(value) for value in headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for key, label in MECHANISM_FIELDS:
        values: list[str] = []
        for sample in context.samples:
            mechanism = mechanisms.get(_content_id(sample), {})
            fields = mechanism.get("fields") if isinstance(mechanism.get("fields"), dict) else {}
            values.append(_mechanism_cell(fields.get(key), context))
        if any(values):
            lines.append("| " + " | ".join(_cell(value) for value in [label, *values]) + " |")
    lines.append(
        "| 互动数据 | "
        + " | ".join(
            _cell(_interaction_text(sample))
            for sample in context.samples
        )
        + " |"
    )
    lines.append("")
    return lines


def _render_claim(item: Any, context: ReportContext) -> str:
    if not isinstance(item, dict):
        return ""
    claim_type = str(item.get("type") or "观察")
    ids = _valid_ids(item.get("evidence_ids"), context)
    if not ids or (claim_type == "推断" and len(ids) < 2):
        return ""
    text = _guard_context_wording(_plain(item.get("text")), context)
    if not text:
        return ""
    uncertainty = _guard_context_wording(_plain(item.get("uncertainty")), context)
    if claim_type == "推断":
        if not uncertainty:
            return ""
        text = f"{text.rstrip('。；; ')}；证据边界：{uncertainty}"
    return f"- {_claim_label(claim_type)} {text} {_links(ids, context)}".rstrip()


def _render_strategy_item(item: Any, context: ReportContext, force_suggestion: bool = False) -> str:
    if not isinstance(item, dict):
        return ""
    ids = _valid_ids(item.get("evidence_ids"), context)
    text = _guard_context_wording(_plain(item.get("text")), context)
    if not text or not ids:
        return ""
    label = "[建议]" if force_suggestion else _claim_label(item.get("type"))
    return f"- {label} {text} {_links(ids, context)}"


def _render_test(item: dict[str, Any], context: ReportContext) -> list[str]:
    ids = _valid_ids(item.get("evidence_ids"), context)
    variable = _guard_test_wording(_plain(item.get("test_variable")), context)
    version_a = _guard_test_wording(_plain(item.get("version_a")), context)
    version_b = _guard_test_wording(_plain(item.get("version_b")), context)
    metric = _plain(item.get("metric"))
    evidence_basis = _guard_test_wording(_plain(item.get("evidence_basis")), context)
    if not all((ids, variable, version_a, version_b, metric, evidence_basis)):
        return []
    return [
        f"#### [建议] {variable}",
        "",
        f"- 测试变量：{variable}",
        f"- A 版本：{version_a}",
        f"- B 版本：{version_b}",
        f"- 观察指标：{metric}",
        f"- 证据依据：{evidence_basis} {_links(ids, context)}".rstrip(),
        "",
    ]


def _mechanism_cell(item: Any, context: ReportContext) -> str:
    if not isinstance(item, dict):
        return ""
    claim_type = str(item.get("type") or "观察")
    if claim_type != "观察" or not _valid_ids(item.get("evidence_ids"), context):
        return ""
    text = _guard_context_wording(_plain(item.get("text")), context)
    placeholders = ("现有证据未明确", "现有证据不足", "证据不足", "无法具体判断", "待补充")
    return "" if not text or any(marker in text for marker in placeholders) else text


def _appendix_by_id(analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in _list(analysis.get("evidence_appendix")):
        if isinstance(item, dict) and item.get("content_id"):
            result[str(item["content_id"])] = item
    return result


def _mechanisms_by_id(analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in _list(analysis.get("content_mechanisms")):
        if isinstance(item, dict) and item.get("content_id"):
            result[str(item["content_id"])] = item
    return result


def _sample_map(context: ReportContext) -> dict[str, ReelSample]:
    return {_content_id(sample): sample for sample in context.samples}


def _valid_ids(value: Any, context: ReportContext) -> list[str]:
    known = _sample_map(context)
    return _deduplicate([str(item) for item in _list(value) if str(item) in known])


def _links(ids: list[str], context: ReportContext) -> str:
    samples = _sample_map(context)
    return "、".join(_link(content_id, samples[content_id].reel.url) for content_id in ids)


def _link(content_id: str, url: str) -> str:
    label = _plain(content_id).replace("[", "").replace("]", "") or "原始内容"
    target = _plain_url(url)
    return f"[{label}]({target})" if target else label


def _plain_url(value: Any) -> str:
    text = str(value or "").strip().replace(" ", "")
    match = re.fullmatch(r"https?://[^<>\[\]]+", text)
    return text if match else ""


def _claim_label(value: Any) -> str:
    if str(value) == "推断":
        return "[推断]"
    if str(value) == "建议":
        return "[建议]"
    return "[观察]"


def _content_id(sample: ReelSample) -> str:
    return sample.reel.shortcode or _id_from_url(sample.reel.url)


def _id_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1] or "Instagram"


def _fallback_summary(sample: ReelSample) -> str:
    if not sample.transcript:
        return ""
    text = " ".join(sample.transcript.text.split())
    return text[:180] + ("…" if len(text) > 180 else "")


def _guard_wording(text: str, sample_count: int) -> str:
    if sample_count >= 5:
        return text
    for forbidden, replacement in FORBIDDEN_SMALL_SAMPLE_PHRASES.items():
        text = text.replace(forbidden, replacement)
    return text


def _guard_context_wording(text: str, context: ReportContext) -> str:
    text = _guard_wording(text, context.sample_count)
    if any(sample.reel.published_at is None for sample in context.samples) and "发布时间与曝光窗口不同" in text:
        return "发布时间信息不完整，且已知样本的曝光窗口可能不同，因此不能直接比较互动表现。"
    return text


def _guard_test_wording(text: str, context: ReportContext) -> str:
    text = _guard_context_wording(text, context)
    if context.sample_count == 1:
        text = text.replace("可能提升互动", "需要通过对照测试评估互动差异")
        text = text.replace("可提升互动", "需要通过对照测试评估互动差异")
    return text


def _plain(value: Any) -> str:
    text = " ".join(str(value or "").split())
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\[([^\]]+)\]\((?:https?://)?[^)]+\)", r"\1", text)
    return text.replace("\n", " ")


def _cell(value: Any) -> str:
    return " ".join(str(value or "").split()).replace("|", "\\|")


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _number(value: int | None) -> str:
    return f"{value:,}" if value is not None else ""


def _interaction_text(sample: ReelSample) -> str:
    values: list[str] = []
    if sample.reel.published_at:
        values.append(f"发布 {sample.reel.published_at.date().isoformat()}")
    for label, value in (
        ("观看", sample.reel.views),
        ("点赞", sample.reel.likes),
        ("评论", sample.reel.comments),
    ):
        if value is not None:
            values.append(f"{label} {value:,}")
    return "；".join(values)


def _render_products(value: Any) -> str:
    products = [_plain(product) for product in _list(value) if _plain(product)]
    return "、".join(_deduplicate(products)) if products else "未识别到明确产品提及"


def _visual_value(value: Any) -> str:
    if isinstance(value, list):
        items = [_plain(item) for item in value if _plain(item)]
        return "；".join(items)
    return _plain(value)


def _visual_failure_reason(diagnostic: dict[str, Any]) -> str:
    failures = diagnostic.get("failures")
    if isinstance(failures, list) and failures:
        return "；".join(
            _friendly_failure(failure)
            for failure in failures
            if isinstance(failure, dict)
        )
    return _friendly_failure(diagnostic) if diagnostic else ""


def _friendly_failure(failure: dict[str, Any]) -> str:
    stage = str(failure.get("failure_stage") or "")
    labels = {
        "DNS": "DNS 解析失败",
        "connection": "网络连接失败",
        "timeout": "请求超时",
        "HTTP": "媒体服务器拒绝请求",
        "file_format": "媒体文件格式无法识别",
        "ffprobe": "无法读取视频时长",
        "ffmpeg": "视频截帧失败",
        "vision_model": "视觉模型分析失败",
        "media_metadata": "未获得可用媒体元数据",
    }
    if failure.get("download_object") == "video":
        target = "视频"
    elif failure.get("download_object") == "cover":
        target = "封面"
    else:
        target = "视觉处理"
    reason = labels.get(stage, "视觉素材处理失败")
    status = failure.get("status_code")
    return f"{target}：{reason}{f'（HTTP {status}）' if status is not None else ''}"


def _username_from_url(url: str) -> str:
    match = re.search(r"instagram\.com/([^/?#]+)", url, flags=re.IGNORECASE)
    username = match.group(1) if match else "instagram"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", username)


def _scope_text(context: ReportContext) -> str:
    if context.analysis_mode == "主页":
        return f"该账号的 {context.sample_count} 条公开内容；结论不外推至账号整体。"
    return f"本次指定的 {context.sample_count} 条公开内容；结论不外推至作者账号整体。"
