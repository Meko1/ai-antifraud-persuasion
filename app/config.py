"""运行时配置。全部来自环境变量，密钥绝不硬编码在源码里。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


class ConfigError(RuntimeError):
    """配置缺失或非法。启动期抛出，不做任何默认值兜底。"""


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# ── provider 与 protocol 是闭集，不是自由字符串 ────────────────────────────
#
# 原先这里只判 `provider == "internal"`，**其余任何字符串都落到 PUBLIC_***。
# 于是 `LLM_PROVIDER=internla` 这一个字母的手滑，会让服务安安静静地起来、
# 健康检查一路绿、然后把每一句用户输入发到公网 DeepSeek 上——
# 日志里没有一行说过它换了供应商。协议那一侧同样：只有 `anthropic` 被认出来，
# `anthropicc` 会落进 OpenAI 客户端，上线当天换来一个看不懂的 400。
#
# 金融数据的处理边界不能由拼写决定。**未知值一律启动失败**：
# 一个起不来的服务，比一个把数据发错地方的服务好得多。
PROVIDERS = frozenset({"internal", "public"})
PROTOCOLS = frozenset({"openai", "anthropic"})


def _enum(name: str, value: str, allowed: frozenset, default: str) -> str:
    """闭集校验。不做"猜一个最接近的"——那正是静默兜底的另一种写法。"""
    text = (value or default).strip().lower()
    if text not in allowed:
        raise ConfigError(
            f"{name}={value!r} 不是合法取值。允许的是 {sorted(allowed)}。"
            "拼错会把数据发往错误的供应商或协议，因此这里拒绝启动而不是兜底。"
        )
    return text


@dataclass(frozen=True)
class LLMSettings:
    """provider 抽象，让内网网关和公网接口可以随时对调。

    拿到目标服务器后如果发现内网网关不可达，只需改一个环境变量即可切换，
    不用动任何代码。

    `provider` 与 `protocol` 都是闭集（见上面那两个常量），非法值在
    `load_settings` 里就抛掉了，构造到这里的一定是合法的。
    """

    provider: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float
    # 走哪套协议：openai（ChatCompletions）或 anthropic（Messages）。
    # 不从 base_url 里猜——猜错的代价是上线当天报一个看不懂的 400。
    protocol: str = "openai"

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def summary(self) -> dict:
        """启动日志与 readiness 用的非敏感配置摘要。**不含 api_key。**

        它回答的是运维在出事那一刻唯一想知道的事：这台服务到底在跟谁说话。
        """
        return {
            "provider": self.provider,
            "protocol": self.protocol,
            "model": self.model,
            "base_url": self.base_url,
            "configured": self.configured,
        }


@dataclass(frozen=True)
class Settings:
    port: int
    log_level: str
    enable_docs: bool
    llm: LLMSettings
    # 对局状态由客户端持有并签名（ADR-0003）。密钥缺失时整个防线就是空的，
    # 与其带着一个可伪造的签名上线，不如直接拒绝启动。
    state_signing_secret: str
    redis_url: str
    # 落不落对局语料（ADR-0006）。**默认 false**：留存这件事不该由
    # "忘了配环境变量"来决定方向，"开始存了"必须是一个有人按下去的动作。
    # 打开之后聊天页那句告知会跟着变，两者由 tests/test_transcripts.py 钉在一起。
    transcript_retention: bool
    # 离线演示模式（app/offline.py）。**启动期的显式开关，不是运行期降级**——
    # ADR-0005 否掉的是"悄悄换一个供应商"，而这一个在 /healthz 上报着、
    # 在聊天页第一行写着。8-22 网关停了八小时，路演不会挑好天气来。
    offline_demo: bool
    # 分享卡上印的参赛编号（§8）。没配就空着——分享卡会发到社交平台上，
    # 空一行远好过印一个占位符出去。
    contest_id: str
    # 每分钟每个来源允许开几局 / 打几轮（§P0-6）。0 = 不限，本机调试用。
    # 默认值按"一局最多 12 轮、一局约 3 分钟"定：正常用户够用得多，
    # 一条 curl 循环打不出量来。
    rate_limit_start: int
    rate_limit_turn: int


def load_settings() -> Settings:
    provider = _enum("LLM_PROVIDER", os.getenv("LLM_PROVIDER", ""), PROVIDERS, "internal")
    prefix = "INTERNAL" if provider == "internal" else "PUBLIC"

    secret = os.getenv("STATE_SIGNING_SECRET", "").strip()
    if not secret:
        raise ConfigError(
            "缺少 STATE_SIGNING_SECRET。对局状态由客户端持有并签名，"
            "没有密钥就等于没有防线——拒绝启动。"
        )

    return Settings(
        state_signing_secret=secret,
        redis_url=os.getenv("REDIS_URL", "").strip(),
        transcript_retention=_bool("TRANSCRIPT_RETENTION", False),
        offline_demo=_bool("OFFLINE_DEMO", False),
        contest_id=os.getenv("CONTEST_ID", "").strip(),
        rate_limit_start=int(os.getenv("RATE_LIMIT_START", "20")),
        rate_limit_turn=int(os.getenv("RATE_LIMIT_TURN", "60")),
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
            protocol=_enum(
                f"{prefix}_LLM_PROTOCOL",
                os.getenv(f"{prefix}_LLM_PROTOCOL", ""),
                PROTOCOLS,
                "openai",
            ),
        ),
    )


settings = load_settings()
