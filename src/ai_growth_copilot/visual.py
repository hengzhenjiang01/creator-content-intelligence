"""Reel 视觉证据：临时下载、固定比例关键帧和脱敏诊断。"""

import base64
import json
import mimetypes
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Reel


KEYFRAME_RATIOS = (0.10, 0.50, 0.90)
MAX_MEDIA_BYTES = 500 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 10
VISION_FIELDS = (
    "visible_scene",
    "visual_hook",
    "on_screen_text",
    "product_ui_or_brand_evidence",
    "before_after_or_result_evidence",
    "visual_content_delivery",
    "confidence_and_limits",
)


class VisualError(RuntimeError):
    """不包含媒体 URL 或底层异常正文的安全视觉错误。"""

    def __init__(
        self,
        stage: str,
        exception_category: str,
        *,
        download_object: str = "none",
        status_code: int | None = None,
        failures: list[dict[str, Any]] | None = None,
    ) -> None:
        self.stage = stage
        self.exception_category = exception_category
        self.download_object = download_object
        self.status_code = status_code
        self.failures = failures or []
        super().__init__(self.safe_message())

    def safe_message(self) -> str:
        status = f"，HTTP {self.status_code}" if self.status_code is not None else ""
        return f"视觉处理失败：阶段={self.stage}，对象={self.download_object}，异常={self.exception_category}{status}"

    def to_dict(self, reel: Reel) -> dict[str, Any]:
        return {
            "has_video_url": bool(reel.video_url),
            "has_cover_url": bool(reel.display_url),
            "download_object": self.download_object,
            "failure_stage": self.stage,
            "exception_category": self.exception_category,
            "status_code": self.status_code,
            "failures": self.failures,
        }

    def as_failure(self) -> dict[str, Any]:
        return {
            "download_object": self.download_object,
            "failure_stage": self.stage,
            "exception_category": self.exception_category,
            "status_code": self.status_code,
        }


class DeepSeekVisionAnalyzer:
    ENDPOINT = "https://api.deepseek.com/chat/completions"

    def __init__(self, api_key: str, model: str, timeout: int = 120) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def analyze_reel(self, reel: Reel) -> dict[str, Any]:
        video_failure: VisualError | None = None
        with tempfile.TemporaryDirectory(prefix="ai_growth_vision_") as temp_dir:
            workdir = Path(temp_dir)
            if reel.video_url:
                try:
                    image_paths = _prepare_video(reel.video_url, workdir, self.timeout)
                    source_type = "video_keyframes"
                    source_note = "视频关键帧：固定取视频时长约 10%、50%、90% 位置"
                    download_object = "video"
                except VisualError as exc:
                    video_failure = exc
                    if not reel.display_url:
                        raise exc
                    image_paths = _prepare_cover_with_fallback(reel.display_url, workdir, self.timeout, exc)
                    source_type = "cover_only"
                    source_note = _cover_fallback_note(exc)
                    download_object = "cover"
            elif reel.display_url:
                image_paths = _prepare_cover(reel.display_url, workdir, self.timeout)
                source_type = "cover_only"
                source_note = "仅分析封面，未取得视频关键帧"
                download_object = "cover"
            else:
                raise VisualError("media_metadata", "MissingMediaFields")

            try:
                result = self._analyze_images(image_paths, source_type)
            except VisualError as exc:
                raise exc
            result.update(
                {
                    "source_type": source_type,
                    "source_note": source_note,
                    "frame_ratios": list(KEYFRAME_RATIOS) if source_type == "video_keyframes" else [],
                    "has_video_url": bool(reel.video_url),
                    "has_cover_url": bool(reel.display_url),
                    "download_object": download_object,
                    "video_fallback_failure": video_failure.as_failure() if video_failure else None,
                }
            )
            return result

    def _analyze_images(self, image_paths: list[Path], source_type: str) -> dict[str, Any]:
        prompt = (
            "只分析所附静态图片中实际可见的视觉证据。图片来源类型："
            f"{source_type}。不得利用 caption、口播、账号背景或常识补全画面；"
            "不得根据三张静帧断言完整视频流程、因果顺序或未展示的动作。"
            "若文字模糊、品牌不确定或前后对比无法从静帧确认，必须在 confidence_and_limits 中直说。"
            "只返回 JSON，字段为 visible_scene、visual_hook、on_screen_text、"
            "product_ui_or_brand_evidence、before_after_or_result_evidence、"
            "visual_content_delivery、confidence_and_limits。每个字段使用字符串或字符串数组。"
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_path in image_paths:
            content.append({"type": "image_url", "image_url": {"url": _image_data_url(image_path), "detail": "high"}})
        try:
            response = requests.post(
                self.ENDPOINT,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是视觉证据记录员。只能描述输入图片直接可见的内容。"
                                "你不能调用工具、访问网络、下载视频或执行截图。仅输出合法 JSON。"
                            ),
                        },
                        {"role": "user", "content": content},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.0,
                },
                timeout=(CONNECT_TIMEOUT_SECONDS, self.timeout),
            )
            response.raise_for_status()
            payload = response.json()
            result = json.loads(payload["choices"][0]["message"]["content"])
        except requests.RequestException as exc:
            raise _request_error(exc, "vision_model", "none") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise VisualError("vision_model", exc.__class__.__name__) from exc
        if not isinstance(result, dict):
            raise VisualError("vision_model", "InvalidJsonRoot")
        for field in VISION_FIELDS:
            result.setdefault(field, "未返回")
        return result


def keyframe_timestamps(duration_seconds: float) -> tuple[float, float, float]:
    if duration_seconds <= 0:
        raise VisualError("ffprobe", "InvalidDuration", download_object="video")
    values = tuple(duration_seconds * ratio for ratio in KEYFRAME_RATIOS)
    return values[0], values[1], values[2]


def _prepare_video(media_url: str, workdir: Path, timeout: int) -> list[Path]:
    video_path = workdir / "source_video.mp4"
    _download_media(media_url, video_path, timeout, "video")
    duration = _probe_duration(video_path)
    return _extract_keyframes(video_path, duration, workdir)


def _prepare_cover(media_url: str, workdir: Path, timeout: int) -> list[Path]:
    cover_path = workdir / "cover.jpg"
    _download_media(media_url, cover_path, timeout, "cover")
    return [cover_path]


def _prepare_cover_with_fallback(
    media_url: str,
    workdir: Path,
    timeout: int,
    video_failure: VisualError,
) -> list[Path]:
    try:
        return _prepare_cover(media_url, workdir, timeout)
    except VisualError as cover_failure:
        combined = VisualError(
            cover_failure.stage,
            cover_failure.exception_category,
            download_object="cover",
            status_code=cover_failure.status_code,
            failures=[video_failure.as_failure(), cover_failure.as_failure()],
        )
        raise combined from cover_failure


def _download_media(media_url: str, destination: Path, timeout: int, download_object: str) -> None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "video/*,image/*;q=0.9,*/*;q=0.5",
        "Referer": "https://www.instagram.com/",
    }
    try:
        with _media_session() as session:
            with session.get(
                media_url,
                headers=headers,
                stream=True,
                timeout=(CONNECT_TIMEOUT_SECONDS, timeout),
                allow_redirects=True,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                downloaded = 0
                with destination.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > MAX_MEDIA_BYTES:
                            raise VisualError("file_format", "MediaTooLarge", download_object=download_object)
                        output.write(chunk)
        _validate_media_file(destination, content_type, download_object)
    except VisualError:
        raise
    except requests.RequestException as exc:
        raise _request_error(exc, _request_failure_stage(exc), download_object) from exc


def _media_session() -> requests.Session:
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def _validate_media_file(path: Path, content_type: str, download_object: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise VisualError("file_format", "EmptyMedia", download_object=download_object)
    signature = path.read_bytes()[:16]
    if download_object == "video":
        valid_magic = b"ftyp" in signature or signature.startswith(b"\x1aE\xdf\xa3")
        valid_type = content_type.startswith("video/")
    else:
        valid_magic = (
            signature.startswith(b"\xff\xd8\xff")
            or signature.startswith(b"\x89PNG\r\n\x1a\n")
            or signature.startswith((b"GIF87a", b"GIF89a"))
            or (signature.startswith(b"RIFF") and b"WEBP" in signature)
            or b"ftypavif" in signature
        )
        valid_type = content_type.startswith("image/")
    if not valid_magic and not valid_type:
        raise VisualError("file_format", "UnexpectedMediaFormat", download_object=download_object)


def _request_failure_stage(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.HTTPError):
        return "HTTP"
    if isinstance(exc, requests.ConnectionError):
        text = str(exc).lower()
        if any(
            marker in text
            for marker in ("name resolution", "nameresolution", "failed to resolve", "getaddrinfo", "nodename", "dns")
        ):
            return "DNS"
        return "connection"
    return "connection"


def _request_error(exc: requests.RequestException, stage: str, download_object: str) -> VisualError:
    status = exc.response.status_code if exc.response is not None else None
    return VisualError(stage, exc.__class__.__name__, download_object=download_object, status_code=status)


def _probe_duration(video_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise VisualError("ffprobe", "ExecutableNotFound", download_object="video")
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        duration = float(completed.stdout.strip())
    except subprocess.TimeoutExpired as exc:
        raise VisualError("ffprobe", "TimeoutExpired", download_object="video") from exc
    except (subprocess.SubprocessError, ValueError) as exc:
        raise VisualError("ffprobe", exc.__class__.__name__, download_object="video") from exc
    if duration <= 0:
        raise VisualError("ffprobe", "InvalidDuration", download_object="video")
    return duration


def _extract_keyframes(video_path: Path, duration: float, workdir: Path) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VisualError("ffmpeg", "ExecutableNotFound", download_object="video")
    paths: list[Path] = []
    for index, timestamp in enumerate(keyframe_timestamps(duration), start=1):
        frame_path = workdir / f"frame_{index}.jpg"
        try:
            subprocess.run(
                [ffmpeg, "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", "-y", str(frame_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise VisualError("ffmpeg", "TimeoutExpired", download_object="video") from exc
        except subprocess.SubprocessError as exc:
            raise VisualError("ffmpeg", exc.__class__.__name__, download_object="video") from exc
        if not frame_path.is_file() or frame_path.stat().st_size == 0:
            raise VisualError("ffmpeg", "EmptyFrame", download_object="video")
        paths.append(frame_path)
    return paths


def _cover_fallback_note(video_failure: VisualError) -> str:
    if video_failure.stage in {"DNS", "connection", "timeout", "HTTP", "file_format"}:
        return "仅分析封面：视频关键帧下载失败"
    return f"仅分析封面：视频关键帧处理失败（{video_failure.stage}）"


def _image_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
