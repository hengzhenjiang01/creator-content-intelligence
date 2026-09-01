from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from .models import Reel


class InstagramScraperError(RuntimeError):
    pass


class InstagramScraper:
    API_BASE = "https://api.apify.com/v2"

    def __init__(self, token: str, actor_id: str, timeout: int = 120) -> None:
        self.token = token
        self.actor_id = actor_id
        self.timeout = timeout

    def fetch_recent_reels(self, account_url: str, limit: int = 3) -> list[Reel]:
        reels_url = account_url.rstrip("/") + "/reels/"
        payload = {
            "directUrls": [reels_url],
            "resultsType": "posts",
            "resultsLimit": max(limit, 3),
            "addParentData": False,
        }
        items = self._run_actor(payload)
        reels = [_to_reel(item) for item in items if isinstance(item, dict) and _is_reel(item)]
        reels.sort(key=lambda reel: reel.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        unique = _unique_reels(reels)
        if not unique:
            raise InstagramScraperError("没有从该公开主页获取到 Reel")
        return unique[:limit]

    def fetch_specified_posts(self, post_urls: list[str]) -> list[Reel]:
        """只获取指定内容的公开元数据，不扫描创作者主页。"""
        payload = {
            "directUrls": post_urls,
            "resultsType": "details",
            "resultsLimit": len(post_urls),
            "addParentData": True,
        }
        items = self._run_actor(payload)
        reels = _unique_reels([_to_reel(item) for item in items if isinstance(item, dict)])
        requested_ids = [url.rstrip("/").split("/")[-1] for url in post_urls]
        by_id = {reel.shortcode: reel for reel in reels if reel.shortcode}
        matched = [by_id[content_id] for content_id in requested_ids if content_id in by_id]
        if not matched:
            raise InstagramScraperError("没有获取到任何指定 Reel/Post 的公开元数据")
        return matched

    def _run_actor(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        actor_id = quote(self.actor_id, safe="")
        endpoint = f"{self.API_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
        try:
            response = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.token}"},
                params={"format": "json", "clean": "true"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            items = response.json()
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else "网络错误"
            raise InstagramScraperError(f"Apify 请求失败（{status}）") from exc
        except ValueError as exc:
            raise InstagramScraperError("Apify 返回了无效 JSON") from exc
        if not isinstance(items, list):
            raise InstagramScraperError("Apify 返回了非预期的数据格式")
        return items


def _is_reel(item: dict[str, Any]) -> bool:
    url = str(item.get("url") or item.get("inputUrl") or "").lower()
    item_type = str(item.get("type") or item.get("productType") or "").lower()
    return "/reel/" in url or item_type in {"video", "clips", "reel"}


def _to_reel(item: dict[str, Any]) -> Reel:
    url = str(item.get("url") or item.get("inputUrl") or "")
    url_content_id = url.rstrip("/").split("/")[-1] if url else ""
    shortcode = str(item.get("shortCode") or item.get("shortcode") or url_content_id or item.get("id") or "")
    if not url and shortcode:
        url = f"https://www.instagram.com/reel/{shortcode}/"
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    parent = item.get("parentData") if isinstance(item.get("parentData"), dict) else {}
    author = str(
        item.get("ownerUsername")
        or item.get("username")
        or owner.get("username")
        or owner.get("userName")
        or parent.get("username")
        or parent.get("ownerUsername")
        or ""
    ).lstrip("@")
    return Reel(
        url=url,
        shortcode=shortcode,
        author_username=author,
        caption=str(item.get("caption") or ""),
        published_at=_parse_datetime(item.get("timestamp") or item.get("takenAt") or item.get("takenAtIso")),
        likes=_as_int(item.get("likesCount")),
        comments=_as_int(item.get("commentsCount")),
        views=_as_int(item.get("videoViewCount") or item.get("videoPlayCount") or item.get("playCount")),
        video_url=str(
            item.get("videoUrl")
            or item.get("video_url")
            or item.get("videoPlayUrl")
            or item.get("videoDownloadUrl")
            or ""
        ),
        display_url=str(
            item.get("displayUrl")
            or item.get("display_url")
            or item.get("thumbnailUrl")
            or item.get("imageUrl")
            or ""
        ),
        raw=item,
    )


def _unique_reels(reels: list[Reel]) -> list[Reel]:
    unique: list[Reel] = []
    seen: set[str] = set()
    for reel in reels:
        key = reel.shortcode or reel.url
        if key and key not in seen:
            seen.add(key)
            unique.append(reel)
    return unique


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
