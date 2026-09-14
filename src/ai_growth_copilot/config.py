from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LiveAnalysisSettings:
    enabled: bool = False
    access_password: str = field(default="", repr=False)

    @classmethod
    def from_env(
        cls,
        env_file: Path | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> "LiveAnalysisSettings":
        load_dotenv(dotenv_path=env_file)
        return cls(
            enabled=_source_value("ENABLE_LIVE_ANALYSIS", "false", overrides).casefold() == "true",
            access_password=_source_value("APP_ACCESS_PASSWORD", "", overrides),
        )


@dataclass(frozen=True)
class Settings:
    apify_token: str = field(repr=False)
    supadata_api_key: str = field(repr=False)
    deepseek_api_key: str = field(repr=False)
    apify_actor_id: str = "apify/instagram-scraper"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_vision_model: str = "deepseek-v4-flash-vision-exp"
    request_timeout_seconds: int = 120
    supadata_poll_interval_seconds: float = 1.0
    supadata_max_poll_attempts: int = 120

    @classmethod
    def from_env(
        cls,
        env_file: Path | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> "Settings":
        load_dotenv(dotenv_path=env_file)
        return cls(
            apify_token=_source_value("APIFY_TOKEN", "", overrides),
            supadata_api_key=_source_value("SUPADATA_API_KEY", "", overrides),
            deepseek_api_key=_source_value("DEEPSEEK_API_KEY", "", overrides),
            apify_actor_id=_source_value("APIFY_ACTOR_ID", "apify/instagram-scraper", overrides),
            deepseek_model=_source_value("DEEPSEEK_MODEL", "deepseek-v4-flash", overrides),
            deepseek_vision_model=_source_value(
                "DEEPSEEK_VISION_MODEL", "deepseek-v4-flash-vision-exp", overrides
            ),
            request_timeout_seconds=_positive_int("REQUEST_TIMEOUT_SECONDS", 120, overrides),
            supadata_poll_interval_seconds=_positive_float(
                "SUPADATA_POLL_INTERVAL_SECONDS", 1.0, overrides
            ),
            supadata_max_poll_attempts=_positive_int(
                "SUPADATA_MAX_POLL_ATTEMPTS", 120, overrides
            ),
        )

    def validate(self, require_secrets: bool) -> list[str]:
        errors: list[str] = []
        if require_secrets:
            for name, value in (
                ("APIFY_TOKEN", self.apify_token),
                ("SUPADATA_API_KEY", self.supadata_api_key),
                ("DEEPSEEK_API_KEY", self.deepseek_api_key),
            ):
                if not value:
                    errors.append(f"缺少环境变量 {name}")
        if not self.apify_actor_id:
            errors.append("APIFY_ACTOR_ID 不能为空")
        if not self.deepseek_model:
            errors.append("DEEPSEEK_MODEL 不能为空")
        if not self.deepseek_vision_model:
            errors.append("DEEPSEEK_VISION_MODEL 不能为空")
        return errors


def _source_value(name: str, default: str, overrides: Mapping[str, Any] | None) -> str:
    if overrides is not None and name in overrides:
        return str(overrides[name]).strip()
    return os.getenv(name, default).strip()


def _positive_int(name: str, default: int, overrides: Mapping[str, Any] | None = None) -> int:
    raw = _source_value(name, str(default), overrides)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数") from exc
    if value <= 0:
        raise ConfigError(f"{name} 必须大于 0")
    return value


def _positive_float(name: str, default: float, overrides: Mapping[str, Any] | None = None) -> float:
    raw = _source_value(name, str(default), overrides)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是数字") from exc
    if value <= 0:
        raise ConfigError(f"{name} 必须大于 0")
    return value
