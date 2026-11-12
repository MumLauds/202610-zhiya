"""Configuration comes from the process environment, never from repository secrets."""

import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx


DASHSCOPE_CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"


def validate_url(value: str, name: str) -> None:
    if value:
        try:
            url = urlsplit(value)
            httpx.URL(value)
            valid = (
                url.scheme == "https"
                and bool(url.hostname)
                and url.username is None
                and url.password is None
                and "?" not in value
                and "#" not in value
                and (url.port is None or 1 <= url.port <= 65535)
                and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            )
        except (ValueError, httpx.InvalidURL):
            valid = False
        if not valid:
            raise ValueError(f"{name} must be an HTTPS URL without credentials, query or fragment")

def validate_key(value: str, name: str) -> None:
    if any(not 33 <= ord(char) <= 126 for char in value):
        raise ValueError(f"{name} must contain only visible ASCII characters")


def validate_text(value: str, name: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must be valid UTF-8 text") from None


@dataclass(frozen=True)
class Settings:
    model_api_url: str = field(default=DEEPSEEK_CHAT_URL, repr=False)
    model_api_key: str = field(default="", repr=False)
    model_name: str = "deepseek-flash"
    model_timeout_seconds: float = 20.0
    model_max_tokens: int = 512

    def __post_init__(self) -> None:
        if not math.isfinite(self.model_timeout_seconds) or not 0 < self.model_timeout_seconds <= 20:
            raise ValueError("MODEL_TIMEOUT_SECONDS must be greater than 0 and at most 20")
        if not 1 <= self.model_max_tokens <= 4096:
            raise ValueError("MODEL_MAX_TOKENS must be between 1 and 4096")
        validate_url(self.model_api_url, "MODEL_API_URL")
        validate_key(self.model_api_key, "MODEL_API_KEY")
        validate_text(self.model_name, "MODEL_NAME")

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "20"))
            max_tokens = int(os.getenv("MODEL_MAX_TOKENS", "512"))
        except ValueError:
            raise ValueError("MODEL_TIMEOUT_SECONDS and MODEL_MAX_TOKENS must be valid numbers") from None
        return cls(
            model_api_url=os.getenv("MODEL_API_URL", DEEPSEEK_CHAT_URL),
            model_api_key=os.getenv("MODEL_API_KEY", ""),
            model_name=os.getenv("MODEL_NAME", "deepseek-flash").strip(),
            model_timeout_seconds=timeout,
            model_max_tokens=max_tokens,
        )

    @property
    def model_configured(self) -> bool:
        return bool(self.model_api_url and self.model_api_key and self.model_name.strip())
