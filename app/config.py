"""运行时配置。全部来自环境变量，密钥绝不硬编码在源码里。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LLMSettings:
    """provider 抽象，让内网网关和公网接口可以随时对调。

    拿到目标服务器后如果发现内网网关不可达，只需改一个环境变量即可切换，
    不用动任何代码。
    """

    provider: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


@dataclass(frozen=True)
class Settings:
    port: int
    log_level: str
    enable_docs: bool
    llm: LLMSettings


def load_settings() -> Settings:
    provider = os.getenv("LLM_PROVIDER", "internal").strip().lower()
    prefix = "INTERNAL" if provider == "internal" else "PUBLIC"

    return Settings(
        # 平台强制固定 21818；保留环境变量只是为了本地调试时能换端口
        port=int(os.getenv("PORT", "21818")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        enable_docs=_bool("ENABLE_DOCS", False),
        llm=LLMSettings(
            provider=provider,
            base_url=os.getenv(f"{prefix}_LLM_BASE_URL", "").rstrip("/"),
            api_key=os.getenv(f"{prefix}_LLM_API_KEY", ""),
            model=os.getenv(f"{prefix}_LLM_MODEL", ""),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        ),
    )


settings = load_settings()
