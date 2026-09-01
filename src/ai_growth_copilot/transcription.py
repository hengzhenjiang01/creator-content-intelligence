import time
from typing import Any

import requests

from .models import Transcript


class TranscriptionError(RuntimeError):
    pass


class SupadataTranscriber:
    API_BASE = "https://api.supadata.ai/v1"

    def __init__(self, api_key: str, timeout: int = 120, poll_interval: float = 1.0, max_attempts: int = 120) -> None:
        self.headers = {"x-api-key": api_key}
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_attempts = max_attempts

    def transcribe(self, reel_url: str) -> Transcript:
        try:
            response = requests.get(
                f"{self.API_BASE}/transcript",
                headers=self.headers,
                params={"url": reel_url, "text": "true", "mode": "auto"},
                timeout=self.timeout,
            )
            if response.status_code == 202:
                data = response.json()
                job_id = data.get("jobId")
                if not job_id:
                    raise TranscriptionError("Supadata 异步响应中缺少 jobId")
                data = self._poll(str(job_id))
            else:
                response.raise_for_status()
                data = response.json()
        except requests.RequestException as exc:
            raise TranscriptionError(f"Supadata 请求失败：{exc}") from exc
        except ValueError as exc:
            raise TranscriptionError("Supadata 返回了无效 JSON") from exc
        text = _content_to_text(data.get("content"))
        if not text:
            raise TranscriptionError("Supadata 未返回转录文本")
        return Transcript(reel_url=reel_url, text=text, language=str(data.get("lang") or ""))

    def _poll(self, job_id: str) -> dict[str, Any]:
        for _ in range(self.max_attempts):
            time.sleep(self.poll_interval)
            try:
                response = requests.get(
                    f"{self.API_BASE}/transcript/{job_id}",
                    headers=self.headers,
                    timeout=self.timeout,
                )
                if response.status_code in {202, 204}:
                    continue
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as exc:
                raise TranscriptionError(f"Supadata 任务查询失败：{exc}") from exc
            status = str(data.get("status") or "").lower()
            if status in {"queued", "pending", "processing"}:
                continue
            if status in {"failed", "error"}:
                raise TranscriptionError(str(data.get("error") or "Supadata 转录任务失败"))
            return data
        raise TranscriptionError("Supadata 转录任务轮询超时")


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(str(part.get("text") or "").strip() for part in content if isinstance(part, dict)).strip()
    return ""
