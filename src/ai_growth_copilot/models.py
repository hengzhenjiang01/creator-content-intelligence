from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class Reel:
    url: str
    shortcode: str = ""
    author_username: str = ""
    caption: str = ""
    published_at: Optional[datetime] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    views: Optional[int] = None
    video_url: str = field(default="", repr=False, compare=False)
    display_url: str = field(default="", repr=False, compare=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class Transcript:
    reel_url: str
    text: str
    language: str = ""


@dataclass(frozen=True)
class ReelSample:
    reel: Reel
    transcript: Optional[Transcript] = None
    error: Optional[str] = None
    product_relationship: str = "unknown"
    visual_evidence: Optional[dict[str, Any]] = None
    visual_error: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class ReportContext:
    account_url: str
    fetched_at: datetime
    samples: list[ReelSample]
    analysis: dict[str, Any]
    analysis_error: Optional[str] = None
    analysis_mode: str = "主页"
    input_urls: list[str] = field(default_factory=list)
    vision_enabled: bool = False

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def failure_count(self) -> int:
        return sum(1 for sample in self.samples if sample.error)

    @property
    def visual_failure_count(self) -> int:
        if not self.vision_enabled:
            return 0
        return sum(1 for sample in self.samples if not sample.visual_evidence)
