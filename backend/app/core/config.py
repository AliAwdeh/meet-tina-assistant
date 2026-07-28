from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Meet Tina"
    environment: str = "development"
    public_base_url: str = "http://localhost:5000"
    dashboard_base_url: str = "http://localhost:5174"
    api_host: str = "0.0.0.0"
    api_port: int = 5000
    log_level: str = "INFO"
    request_body_limit_bytes: int = 5_242_880

    database_url: str = "sqlite:///./data/meet_tina.db"
    redis_url: str = "redis://redis:6379/0"
    redis_required: bool = False

    data_dir: Path = Path("./data")
    media_max_bytes: int = 25_000_000
    allowed_outbound_hosts: list[str] = Field(default_factory=list)

    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    cookie_secure: bool = True

    ai_base_url: str = "https://langcc.maidstech.ai/v1"
    ai_api_key: str = ""
    ai_chat_model: str = ""
    ai_vision_model: str = ""
    ai_transcription_model: str = ""
    ai_timeout_seconds: int = 120
    ai_max_retries: int = 3
    ai_temperature: float = 0.2
    ai_max_tokens: int = 1200

    openwa_dashboard_url: str = "https://openwa-dashboard.meettina.net"
    openwa_api_base_url: str = ""
    openwa_webhook_secret: str = ""
    openwa_api_token: str = ""
    openwa_session_id: str = ""
    openwa_allowed_instance_id: str = ""
    openwa_replay_window_seconds: int = 300

    n8n_base_url: str = ""
    n8n_email_webhook_url: str = ""
    n8n_callback_secret: str = ""
    n8n_outbound_token: str = ""
    n8n_allowed_source_ips: list[str] = Field(default_factory=list)
    n8n_replay_window_seconds: int = 300

    internal_worker_token: str = "change-worker-token"
    email_approval_mode: Literal["draft_only", "confirm", "auto"] = "auto"
    meeting_preparation_offset_hours: int = 4
    default_timezone: str = "Asia/Beirut"

    @field_validator("data_dir", mode="before")
    @classmethod
    def coerce_data_dir(cls, value: str | Path) -> Path:
        return Path(value)

    @field_validator("allowed_outbound_hosts", "n8n_allowed_source_ips", mode="before")
    @classmethod
    def split_csv(cls, value: str | list[str] | None) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
