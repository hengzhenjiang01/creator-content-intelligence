from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

from .analysis import DeepSeekAnalyzer
from .config import Settings
from .instagram import InstagramScraper
from .models import Reel, ReelSample, ReportContext
from .report import write_report
from .transcription import SupadataTranscriber, TranscriptionError
from .visual import DeepSeekVisionAnalyzer, VisualError


class Pipeline:
    def __init__(self, settings: Settings, output_dir: Path) -> None:
        self.settings = settings
        self.output_dir = output_dir

    def run(
        self,
        account_url: str = "",
        reel_urls: list[str] | None = None,
        focus_products: list[str] | None = None,
        vision: bool = False,
    ) -> Path:
        reel_urls = reel_urls or []
        focus_products = focus_products or []
        if bool(account_url) == bool(reel_urls):
            raise ValueError("必须且只能选择主页模式或指定 Reel 模式")
        fetched_at = datetime.now(timezone.utc).astimezone()
        scraper = InstagramScraper(
            self.settings.apify_token,
            self.settings.apify_actor_id,
            self.settings.request_timeout_seconds,
        )
        if reel_urls:
            fetched_reels = scraper.fetch_specified_posts(reel_urls)
            fetched_by_id = {reel.shortcode: reel for reel in fetched_reels}
            reels = [
                fetched_by_id.get(url.rstrip("/").split("/")[-1])
                or Reel(url=url, shortcode=url.rstrip("/").split("/")[-1])
                for url in reel_urls
            ]
            analysis_mode = "指定 Reel"
            input_urls = reel_urls
            analysis_context = "指定公开内容：" + ", ".join(reel_urls)
        else:
            reels = scraper.fetch_recent_reels(account_url, limit=3)
            analysis_mode = "主页"
            input_urls = [account_url]
            analysis_context = f"创作者主页：{account_url}"
        transcriber = SupadataTranscriber(
            self.settings.supadata_api_key,
            timeout=self.settings.request_timeout_seconds,
            poll_interval=self.settings.supadata_poll_interval_seconds,
            max_attempts=self.settings.supadata_max_poll_attempts,
        )
        samples: list[ReelSample] = []
        for reel in reels:
            if analysis_mode == "指定 Reel" and not reel.raw:
                samples.append(ReelSample(reel=reel, error="Apify 未返回该指定内容的公开元数据"))
                continue
            try:
                samples.append(ReelSample(reel=reel, transcript=transcriber.transcribe(reel.url)))
            except TranscriptionError as exc:
                samples.append(ReelSample(reel=reel, error=str(exc)))
        if vision:
            vision_analyzer = DeepSeekVisionAnalyzer(
                self.settings.deepseek_api_key,
                self.settings.deepseek_vision_model,
                self.settings.request_timeout_seconds,
            )
            visually_enriched: list[ReelSample] = []
            for sample in samples:
                if analysis_mode == "指定 Reel" and not sample.reel.raw:
                    visually_enriched.append(
                        replace(
                            sample,
                            visual_error={
                                "has_video_url": False,
                                "has_cover_url": False,
                                "download_object": "none",
                                "failure_stage": "media_metadata",
                                "exception_category": "MissingPublicMetadata",
                                "status_code": None,
                                "failures": [],
                            },
                        )
                    )
                    continue
                try:
                    evidence = vision_analyzer.analyze_reel(sample.reel)
                    visually_enriched.append(replace(sample, visual_evidence=evidence))
                except VisualError as exc:
                    visually_enriched.append(replace(sample, visual_error=exc.to_dict(sample.reel)))
            samples = visually_enriched
        analyzer = DeepSeekAnalyzer(
            self.settings.deepseek_api_key,
            self.settings.deepseek_model,
            self.settings.request_timeout_seconds,
        )
        analysis_error = None
        try:
            analysis = analyzer.analyze(analysis_context, samples, focus_products=focus_products)
        except Exception as exc:
            analysis = {}
            analysis_error = str(exc)
        return write_report(
            ReportContext(
                account_url=account_url,
                fetched_at=fetched_at,
                samples=samples,
                analysis=analysis,
                analysis_error=analysis_error,
                analysis_mode=analysis_mode,
                input_urls=input_urls,
                vision_enabled=vision,
            ),
            self.output_dir,
        )


def dry_run_summary(
    account_url: str,
    reel_urls: list[str],
    focus_products: list[str],
    settings: Settings,
    output_dir: Path,
    vision: bool = False,
) -> list[str]:
    specified = bool(reel_urls)
    lines = [
        "运行模式：dry-run（不会调用任何外部 API）",
        f"分析模式：{'指定 Reel' if specified else '主页'}",
        f"视觉分析：{'已请求（dry-run 不下载、不调用视觉模型）' if vision else '未启用'}",
    ]
    if specified:
        lines.extend(f"指定输入 {index}：{url}" for index, url in enumerate(reel_urls, start=1))
        lines.append("预计报告路径：" + str((output_dir / "specified_reels_YYYYMMDD_HHMMSS.md").resolve()))
        flow = f"Apify 仅获取指定 {len(reel_urls)} 条内容元数据 → Supadata 逐条转录"
    else:
        lines.append(f"Instagram 主页 URL：{account_url}")
        lines.append("预计报告路径：" + str((output_dir / "<username>_YYYYMMDD_HHMMSS.md").resolve()))
        flow = "Apify 最近 3 条 Reel → Supadata 逐条转录"
    if vision:
        flow += " → 临时下载媒体 → 固定 10%/50%/90% 关键帧（无视频时仅封面）→ DeepSeek Vision"
    flow += " → DeepSeek 证据约束分析"
    lines.extend(
        [
            f"重点产品：{', '.join(focus_products) if focus_products else '未指定'}",
            f"报告目录：{output_dir.resolve()}",
            f"Apify Actor：{settings.apify_actor_id}",
            f"DeepSeek 模型：{settings.deepseek_model}",
            f"DeepSeek Vision 模型：{settings.deepseek_vision_model}",
            f"流程校验：{flow} → outputs/ Markdown 报告",
            "配置校验：dry-run 不要求真实 API key；使用 --run 时将严格检查三项密钥。",
        ]
    )
    return lines
