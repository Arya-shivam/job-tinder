from pathlib import Path
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://jobmatch:jobmatch@127.0.0.1:5432/jobmatch"
    redis_url: str = "redis://127.0.0.1:6379/0"
    web_origin: str = "http://localhost:38080"
    cookie_secure: bool = False
    board_file: Path = Path(__file__).with_name("boards.toml")
    openai_api_key: str = ""
    openai_base_url: str | None = None
    embedding_model: str = "text-embedding-3-small"
    extraction_model: str = "gpt-4.1-mini"
    chat_model: str = "gpt-4.1"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"

    @field_validator("web_origin")
    @classmethod
    def origin_only(cls, value: str) -> str:
        split = urlsplit(value)
        if (split.scheme not in {"http", "https"} or not split.hostname or split.username
                or split.path not in {"", "/"} or split.query or split.fragment):
            raise ValueError("WEB_ORIGIN must be an HTTP(S) origin, without a path")
        return f"{split.scheme}://{split.netloc}"

    @model_validator(mode="after")
    def secure_cookies_require_https(self):
        if self.web_origin.startswith("https://") and not self.cookie_secure:
            raise ValueError("set COOKIE_SECURE=true for HTTPS deployments")
        return self
