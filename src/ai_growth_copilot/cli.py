import argparse
from pathlib import Path
import sys
from urllib.parse import urlparse

from .config import ConfigError, Settings
from .pipeline import Pipeline, dry_run_summary


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="分析公开 Instagram 创作者内容")
    parser.add_argument("account_url", nargs="?", help="公开 Instagram 创作者主页 URL")
    parser.add_argument(
        "--reel",
        action="append",
        default=[],
        metavar="URL",
        help="指定一个 Instagram /reel/、/reels/ 或 /p/ 内容 URL；可重复 1–3 次",
    )
    parser.add_argument(
        "--focus-products",
        nargs="+",
        default=[],
        metavar="PRODUCT",
        help="提醒模型优先识别的产品名；不会将其视为已提及",
    )
    parser.add_argument(
        "--vision",
        action="store_true",
        help="启用视频关键帧/封面视觉分析；只有与 --run 同时使用时才下载媒体并调用视觉模型",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="显式允许调用 Apify、Supadata 和 DeepSeek；不加该参数时仅 dry-run",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs",
        help="报告输出目录（默认：项目 outputs/）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.account_url and args.reel:
        parser.error("创作者主页 URL 与 --reel 不可同时使用，请选择一种分析模式")
    if not args.account_url and not args.reel:
        parser.error("必须提供创作者主页 URL，或使用 --reel 提供 1–3 条指定内容 URL")
    if len(args.reel) > 3:
        parser.error("--reel 最多可重复 3 次")
    try:
        account_url = validate_instagram_profile_url(args.account_url) if args.account_url else ""
        reel_urls = [normalize_instagram_content_url(url) for url in args.reel]
        focus_products = normalize_focus_products(args.focus_products)
        settings = Settings.from_env(PROJECT_ROOT / ".env")
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    errors = settings.validate(require_secrets=args.run)
    if errors:
        for error in errors:
            print(f"配置错误：{error}", file=sys.stderr)
        return 2
    if not args.run:
        print("\n".join(dry_run_summary(account_url, reel_urls, focus_products, settings, args.output_dir, args.vision)))
        return 0
    print("已启用 --run：即将调用外部 API。")
    try:
        report_path = Pipeline(settings, args.output_dir).run(
            account_url=account_url,
            reel_urls=reel_urls,
            focus_products=focus_products,
            vision=args.vision,
        )
    except Exception as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1
    print(f"报告已生成：{report_path.resolve()}")
    return 0


def validate_instagram_profile_url(value: str) -> str:
    candidate = value.strip()
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {"instagram.com", "www.instagram.com"}:
        raise ConfigError("请输入 http(s)://www.instagram.com/<username>/ 格式的 URL")
    parts = [part for part in parsed.path.split("/") if part]
    reserved = {"reel", "reels", "p", "stories", "explore", "accounts", "direct"}
    if len(parts) != 1 or parts[0].lower() in reserved:
        raise ConfigError("URL 必须指向 Instagram 创作者主页，而不是帖子、Reel 或其他页面")
    return f"https://www.instagram.com/{parts[0]}/"


def normalize_instagram_content_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {"instagram.com", "www.instagram.com"}:
        raise ConfigError("--reel 必须是 instagram.com 的 http(s) URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].lower() not in {"reel", "reels", "p"} or not parts[1]:
        raise ConfigError("--reel 仅接受 /reel/内容ID/、/reels/内容ID/ 或 /p/内容ID/ URL")
    route = "reel" if parts[0].lower() in {"reel", "reels"} else "p"
    return f"https://www.instagram.com/{route}/{parts[1]}/"


def normalize_focus_products(values: list[str]) -> list[str]:
    products: list[str] = []
    seen: set[str] = set()
    for value in values:
        product = " ".join(value.split()).strip()
        key = product.casefold()
        if product and key not in seen:
            seen.add(key)
            products.append(product)
    return products
