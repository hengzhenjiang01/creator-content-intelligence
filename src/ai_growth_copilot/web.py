from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, MutableMapping

from .cli import normalize_focus_products, normalize_instagram_content_url, validate_instagram_profile_url
from .config import ConfigError, Settings
from .pipeline import Pipeline, PipelineResult, ProgressCallback


class WebInputError(ValueError):
    pass


@dataclass(frozen=True)
class WebAnalysisRequest:
    account_url: str
    reel_urls: list[str]
    focus_products: list[str]
    vision: bool


@dataclass(frozen=True)
class ReportAppendix:
    title: str
    markdown: str


@dataclass(frozen=True)
class WebReportView:
    body_markdown: str
    appendices: list[ReportAppendix]


def prepare_web_request(
    account_url: str,
    reel_text: str,
    focus_text: str,
    vision: bool,
) -> WebAnalysisRequest:
    profile = account_url.strip()
    reel_values = [value for value in re.split(r"[\s,，]+", reel_text.strip()) if value]
    if profile and reel_values:
        raise WebInputError("创作者主页 URL 与指定 Reel URL 不可同时提交，请选择一种输入模式")
    if not profile and not reel_values:
        raise WebInputError("请输入创作者主页 URL，或粘贴 1–3 条 Reel/Post URL")
    if len(reel_values) > 3:
        raise WebInputError("指定 Reel/Post URL 最多为 3 条")
    try:
        normalized_profile = validate_instagram_profile_url(profile) if profile else ""
        normalized_reels = [normalize_instagram_content_url(value) for value in reel_values]
    except ConfigError as exc:
        raise WebInputError(str(exc)) from exc
    if len(set(normalized_reels)) != len(normalized_reels):
        raise WebInputError("指定内容中存在重复 URL，请删除重复项后再提交")
    focus_values = [value.strip() for value in re.split(r"[,，;；\n]+", focus_text) if value.strip()]
    return WebAnalysisRequest(
        account_url=normalized_profile,
        reel_urls=normalized_reels,
        focus_products=normalize_focus_products(focus_values),
        vision=bool(vision),
    )


def execute_web_request(
    request: WebAnalysisRequest,
    settings: Settings,
    output_dir: Path,
    progress_callback: ProgressCallback | None = None,
    pipeline_class: type[Pipeline] = Pipeline,
) -> PipelineResult:
    errors = settings.validate(require_secrets=True)
    if errors:
        raise WebInputError("；".join(errors))
    return pipeline_class(settings, output_dir).run_with_result(
        account_url=request.account_url,
        reel_urls=request.reel_urls,
        focus_products=request.focus_products,
        vision=request.vision,
        progress_callback=progress_callback,
    )


def request_fingerprint(request: WebAnalysisRequest) -> str:
    payload = {
        "account_url": request.account_url,
        "reel_urls": request.reel_urls,
        "focus_products": request.focus_products,
        "vision": request.vision,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def claim_request(state: MutableMapping[str, Any], fingerprint: str) -> bool:
    if state.get("analysis_running"):
        return False
    state["analysis_running"] = True
    state["active_request_fingerprint"] = fingerprint
    return True


def release_request(state: MutableMapping[str, Any]) -> None:
    state["analysis_running"] = False
    state.pop("active_request_fingerprint", None)


def safe_web_error(exc: BaseException) -> str:
    if isinstance(exc, (WebInputError, ConfigError, ValueError)):
        return _redact_text(str(exc))
    if exc.__class__.__name__ in {"InstagramScraperError", "TranscriptionError", "AnalysisError"}:
        return _redact_text(str(exc))
    return f"分析未完成（{exc.__class__.__name__}），请检查网络、公开内容状态或本地配置后重试。"


def result_issue_lines(result: PipelineResult) -> list[str]:
    issues: list[str] = []
    for sample in result.context.samples:
        content_id = sample.reel.shortcode or "内容"
        if sample.error:
            issues.append(f"{content_id}：{_redact_text(sample.error)}")
        if result.context.vision_enabled and not sample.visual_evidence:
            issues.append(f"{content_id}：视觉证据缺失")
    if result.context.analysis_error:
        issues.append(f"综合分析：{_redact_text(result.context.analysis_error)}")
    return issues


def parse_report_for_web(report_text: str) -> WebReportView:
    """将 V3 Markdown 拆成决策正文与原生 Streamlit 折叠附录。"""
    text = report_text.replace("\r\n", "\n")
    body_start = re.search(r"^## A\. 核心结论\s*$", text, flags=re.MULTILINE)
    appendix_start = re.search(r"^## D\. 原始证据附录\s*$", text, flags=re.MULTILINE)
    if body_start:
        body_end = appendix_start.start() if appendix_start else len(text)
        body = text[body_start.start():body_end].strip()
    else:
        body = ""

    appendix_text = text[appendix_start.end():] if appendix_start else ""
    appendices: list[ReportAppendix] = []
    pattern = re.compile(
        r"<details>\s*<summary>(.*?)</summary>\s*(.*?)\s*</details>",
        flags=re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(appendix_text):
        title = _plain_expander_title(match.group(1))
        markdown = _strip_detail_tags(match.group(2)).strip()
        if title and markdown:
            appendices.append(ReportAppendix(title=title, markdown=markdown))
    return WebReportView(body_markdown=body, appendices=appendices)


def _redact_text(value: str) -> str:
    text = re.sub(r"https?://\S+", "[URL 已隐藏]", value)
    text = re.sub(r"(?i)(token|key|authorization|signature|sig)=?[^\s,;，；]+", r"\1=[已隐藏]", text)
    text = re.sub(r"\b(?:sk-|gh[opsu]_|apify_api_)[A-Za-z0-9_-]{8,}\b", "[凭据已隐藏]", text)
    return " ".join(text.split())[:500]


def _plain_expander_title(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    return " ".join(text.split())[:120]


def _strip_detail_tags(value: str) -> str:
    return re.sub(r"</?(?:details|summary)>", "", value, flags=re.IGNORECASE)
