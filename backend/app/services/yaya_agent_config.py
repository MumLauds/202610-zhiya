"""Server-side migration policy and a dedicated Beijing model configuration."""

import os
import re
from dataclasses import dataclass, field

from app.config import Settings

AGENT_VERSION = "2.0"
MODEL = "qwen3.8-flash"
WORKSPACE = re.compile(r"https://(?:ws|llm)-[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com/compatible-mode/v1/chat/completions")


@dataclass(frozen=True)
class YayaSettings:
    mode: str = "off"
    guardians: frozenset[int] = frozenset()
    model: Settings | None = field(default=None, repr=False)
    ledger_secret: str = field(default="", repr=False)
    daily_micros: int = 10_000_000
    guardian_calls: int = 40
    session_calls: int = 20
    concurrency: int = 6
    retention_days: int = 2
    model_input_guardians: frozenset[int] = field(default=frozenset(), repr=False)

    def __post_init__(self):
        if self.mode not in {"off", "allowlist", "default"}:
            raise ValueError("Invalid YAYA_AGENT2_MODE")
        if self.mode == "allowlist" and not self.guardians:
            raise ValueError("YAYA_AGENT2_ALLOWLIST is required for allowlist mode")
        if self.mode != "off" and (self.model is None or not self.model.model_configured
                                   or len(self.ledger_secret.encode()) < 32):
            raise ValueError("Agent 2.0 needs its own model and a ledger secret")
        if self.model and (self.model.model_name != MODEL or not WORKSPACE.fullmatch(self.model.model_api_url)):
            raise ValueError("Agent 2.0 requires the Beijing workspace and the fixed Flash model")
        if (not 1 <= self.daily_micros <= 1_000_000_000
                or not 1 <= self.session_calls <= self.guardian_calls <= 1000
                or not 1 <= self.concurrency <= 64 or not 2 <= self.retention_days <= 7):
            raise ValueError("Invalid Agent 2.0 budget or retention")

    def enrolls(self, guardian_id: int) -> bool:
        return self.mode == "default" or (self.mode == "allowlist" and guardian_id in self.guardians)

    def authorizes_model_input(self, guardian_id: int) -> bool:
        return self.enrolls(guardian_id) and guardian_id in self.model_input_guardians

    @classmethod
    def from_env(cls):
        mode = os.getenv("YAYA_AGENT2_MODE", "off")
        if mode == "off":
            return cls()
        if mode == "default" and os.getenv("YAYA_AGENT2_RELEASE_APPROVED") != "true":
            raise ValueError("Agent 2.0 default enrollment requires an approved release")
        if os.getenv("YAYA_AGENT2_STAGE") == "staging" and mode != "allowlist":
            raise ValueError("Staging requires allowlist mode")
        key = os.getenv("YAYA_AGENT2_API_KEY", "")
        if key and key in (os.getenv("MODEL_API_KEY"), os.getenv("DASHSCOPE_API_KEY")):
            raise ValueError("Agent 2.0 requires a dedicated credential")
        return cls(
            mode=mode,
            guardians=frozenset(int(value.strip()) for value in os.getenv("YAYA_AGENT2_ALLOWLIST", "").split(",") if value.strip()),
            model_input_guardians=frozenset(int(value) for value in os.getenv("YAYA_AGENT2_INPUT_ALLOWLIST", "").split(",")
                                            if value.strip()),
            model=Settings(model_api_url=os.getenv("YAYA_AGENT2_API_URL", ""), model_api_key=key,
                           model_name=MODEL, model_timeout_seconds=3.5, model_max_tokens=256,
                           model_send_thinking=True),
            ledger_secret=os.getenv("YAYA_AGENT2_LEDGER_SECRET", ""),
            daily_micros=int(os.getenv("YAYA_AGENT2_DAILY_MICROS", "10000000")),
            guardian_calls=int(os.getenv("YAYA_AGENT2_GUARDIAN_CALLS", "40")),
            session_calls=int(os.getenv("YAYA_AGENT2_SESSION_CALLS", "20")),
            concurrency=int(os.getenv("YAYA_AGENT2_CONCURRENCY", "6")),
            retention_days=int(os.getenv("YAYA_AGENT2_LEDGER_RETENTION_DAYS", "2")),
        )
