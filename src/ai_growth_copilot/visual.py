"""Reel 视觉证据：临时下载、固定比例关键帧和 DeepSeek Vision 分析。"""

import base64
import json
import mimetypes
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

import requests

from .models import Reel


KEYFRAME_RATIOS = (0.10, 0.50, 0.90)
MAX_MEDIA_BYTES = 500 * 1024 * 1024
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
    pass


class DeepSeekVisionAnalyzer:
    ENDPOINT = "https://api.deepseek.com/chat/completions"

    def __init__(self, api_key: str, model: str, timeout: int = 120) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def analyze_reel(self, reel: Reel) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="ai_growth_vision_") as temp_dir:
            workdir = Path(temp_dir)
            if reel.video_url:
                video_path = workdir / "source_video.mp4"
                _download_media(reel.video_url, video_path, self.timeout)
                duration = _probe_duration(video_path)
                image_paths = _extract_keyframes(video_path, duration, workdir)
                source_type = "video_keyframes"
                source_note = "视频关键帧：固定取视频时长约 10%、50%、90% 位置"
            elif reel.display_url:
                cover_path = workdir / "cover.jpg"
                _download_media(reel.display_url, cover_path, self.timeout)
                image_paths = [cover_path]
                source_type = "cover_only"
                source_note = "仅分析封面，未取得视频关键帧"
            else:
                raise VisualError("Apify 元数据未提供 videoUrl 或 displayUrl")
            result = self._analyze_images(image_paths, source_type)
            result["source_type"] = source_type
            result["source_note"] = source_note
            result["frame_ratios"] = list(KEYFRAME_RATIOS) if source_type == "video_keyframes" else []
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
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(image_path), "detail": "high"},
                }
            )
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
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            result = json.loads(payload["choices"][0]["message"]["content"])
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else "网络错误"
            raise VisualError(f"DeepSeek Vision 请求失败（{status}）") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise VisualError(f"DeepSeek Vision 响应解析失败（{exc.__class__.__name__}）") from exc
        if not isinstance(result, dict):
            raise VisualError("DeepSeek Vision JSON 顶层不是对象")
        for field in VISION_FIELDS:
            result.setdefault(field, "未返回")
        return result


def keyframe_timestamps(duration_seconds: float) -> tuple[float, float, float]:
    if duration_seconds <= 0:
        raise VisualError("视频时长必须大于 0")
    values = tuple(duration_seconds * ratio for ratio in KEYFRAME_RATIOS)
    return values[0], values[1], values[2]


def _download_media(media_url: str, destination: Path, timeout: int) -> None:
    try:
        with requests.get(media_url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            downloaded = 0
            with destination.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_MEDIA_BYTES:
                        raise VisualError("视觉素材超过 500 MB 安全上限")
                    output.write(chunk)
    except VisualError:
        raise
    except requests.RequestException as exc:
        status = exc.response.status_code if exc.response is not None else "网络错误"
        raise VisualError(f"视觉素材下载失败（{status}）") from exc


def _probe_duration(video_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise VisualError("本机未安装 ffprobe，无法读取视频时长")
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        duration = float(completed.stdout.strip())
    except (subprocess.SubprocessError, ValueError) as exc:
        raise VisualError("无法读取视频时长") from exc
    if duration <= 0:
        raise VisualError("视频时长无效")
    return duration


def _extract_keyframes(video_path: Path, duration: float, workdir: Path) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VisualError("本机未安装 ffmpeg，无法截取关键帧")
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
        except subprocess.SubprocessError as exc:
            raise VisualError(f"无法截取第 {index} 张关键帧") from exc
        if not frame_path.is_file() or frame_path.stat().st_size == 0:
            raise VisualError(f"第 {index} 张关键帧为空")
        paths.append(frame_path)
    return paths


def _image_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
