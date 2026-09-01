from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    apify_token: str
    supadata_api_key: str
    deepseek_api_key: str
    apify_actor_id: str = "apify/instagram-scraper"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_vision_model: str = "deepseek-v4-flash-vision-exp"
    request_timeout_seconds: int = 120
    supadata_poll_interval_seconds: float = 1.0
    supadata_max_poll_attempts: int = 120

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        load_dotenv(dotenv_path=env_file)
        return cls(
            apify_token=os.getenv("APIFY_TOKEN", "").strip(),
            supadata_api_key=os.getenv("SUPADATA_API_KEY", "").strip(),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            apify_actor_id=os.getenv("APIFY_ACTOR_ID", "apify/instagram-scraper").strip(),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
            deepseek_vision_model=os.getenv("DEEPSEEK_VISION_MODEL", "deepseek-v4-flash-vision-exp").strip(),
            request_timeout_seconds=_positive_int("REQUEST_TIMEOUT_SECONDS", 120),
            supadata_poll_interval_seconds=_positive_float("SUPADATA_POLL_INTERVAL_SECONDS", 1.0),
            supadata_max_poll_attempts=_positive_int("SUPADATA_MAX_POLL_ATTEMPTS", 120),
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


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数") from exc
    if value <= 0:
        raise ConfigError(f"{name} 必须大于 0")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是数字") from exc
    if value <= 0:
        raise ConfigError(f"{name} 必须大于 0")
    return value
