from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CANVAS_HELPER_",
        env_file=None,
        extra="ignore",
    )

    canvas_base_url: AnyHttpUrl = "https://canvas.uts.edu.au"
    deployment_mode: Literal["local_desktop", "server"] = "local_desktop"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./canvas_helper.db"
    data_dir: Path = Path.home() / ".canvas-helper"
    frontend_dir: Path | None = None
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://testserver",
    )
    local_header_name: str = "X-Canvas-Helper"
    local_header_value: str = "1"
    action_token_ttl_seconds: int = 120
    magic_link_ttl_seconds: int = 900
    session_ttl_seconds: int = 60 * 60 * 24 * 30
    session_rotation_seconds: int = 60 * 60 * 24
    session_cookie_name: str = "canvas_helper_session"
    csrf_cookie_name: str = "canvas_helper_csrf"
    session_cookie_secure: bool = True
    public_url: str = "http://127.0.0.1:5173"
    email_backend: Literal["development", "smtp"] = "development"
    email_from: str = "Canvas Helper <noreply@localhost>"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    credential_encryption_key: str | None = None
    credential_key_version: int = 1
    keyring_service: str = "canvas-helper"
    allow_development_secret_file: bool = False
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    debug: bool = False

    @field_validator("canvas_base_url")
    @classmethod
    def require_https_canvas(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https" or not value.host:
            raise ValueError("Canvas base URL must be HTTPS with a host")
        return value

    @model_validator(mode="after")
    def production_is_fail_closed(self) -> "Settings":
        if self.environment != "production":
            return self
        if self.debug:
            raise ValueError("debug must be disabled in production")
        if self.deployment_mode == "server":
            if not self.credential_encryption_key:
                raise ValueError(
                    "credential_encryption_key is required for production server mode"
                )
            if not self.session_cookie_secure:
                raise ValueError("secure session cookies are required in production")
            if not self.public_url.startswith("https://"):
                raise ValueError("public_url must use HTTPS in production server mode")
            if self.email_backend != "smtp":
                raise ValueError("SMTP email is required in production server mode")
            if not self.smtp_host or not self.smtp_username or not self.smtp_password:
                raise ValueError("complete SMTP credentials are required in production")
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise ValueError("PostgreSQL is required in production server mode")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
