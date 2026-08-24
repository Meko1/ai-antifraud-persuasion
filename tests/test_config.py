"""配置闭集（P1-10）。

原先只判 `provider == "internal"`，**其余任何字符串都落到 PUBLIC_***。
于是 `LLM_PROVIDER=internla` 这一个字母的手滑，会让服务安安静静地起来、
健康检查一路绿、然后把每一句用户输入发到公网上——日志里没有一行说过
它换了供应商。

金融数据的处理边界不能由拼写决定。**未知值一律启动失败**：
一个起不来的服务，比一个把数据发错地方的服务好得多。
"""

import pytest

from app.config import PROTOCOLS, PROVIDERS, ConfigError, LLMSettings, load_settings


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """给每个用例一套干净、完整的环境变量。

    不设的话，`load_settings` 会读到开发机 `.env` 里的真实配置，
    测试就会跟着那台机器的配置走。
    """
    monkeypatch.setenv("STATE_SIGNING_SECRET", "test-only")
    for prefix in ("INTERNAL", "PUBLIC"):
        monkeypatch.setenv(f"{prefix}_LLM_BASE_URL", "https://example.invalid")
        monkeypatch.setenv(f"{prefix}_LLM_API_KEY", "k")
        monkeypatch.setenv(f"{prefix}_LLM_MODEL", "m")
        monkeypatch.setenv(f"{prefix}_LLM_PROTOCOL", "openai")


class Test合法值:
    @pytest.mark.parametrize("provider", sorted(PROVIDERS))
    def test_两个provider都能起(self, monkeypatch, provider) -> None:
        monkeypatch.setenv("LLM_PROVIDER", provider)
        assert load_settings().llm.provider == provider

    @pytest.mark.parametrize("protocol", sorted(PROTOCOLS))
    def test_两套protocol都能起(self, monkeypatch, protocol) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "internal")
        monkeypatch.setenv("INTERNAL_LLM_PROTOCOL", protocol)
        assert load_settings().llm.protocol == protocol

    def test_不设就用默认值(self, monkeypatch) -> None:
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("INTERNAL_LLM_PROTOCOL", raising=False)
        cfg = load_settings().llm
        assert cfg.provider == "internal"
        assert cfg.protocol == "openai"

    def test_大小写与空白不算错(self, monkeypatch) -> None:
        """`LLM_PROVIDER=" Internal "` 是手滑，但它的意图毫无歧义。
        严格是为了挡住**意图不明**的输入，不是为了惩罚空格。"""
        monkeypatch.setenv("LLM_PROVIDER", "  Internal  ")
        assert load_settings().llm.provider == "internal"


class Test非法值一律拒绝启动:
    def test_provider拼错(self, monkeypatch) -> None:
        """复核当时实测的那一个：它会静默选中公网配置。"""
        monkeypatch.setenv("LLM_PROVIDER", "internla")
        with pytest.raises(ConfigError, match="LLM_PROVIDER"):
            load_settings()

    def test_protocol拼错(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "internal")
        monkeypatch.setenv("INTERNAL_LLM_PROTOCOL", "anthropicc")
        with pytest.raises(ConfigError, match="PROTOCOL"):
            load_settings()

    def test_错误信息要说清允许什么(self, monkeypatch) -> None:
        """一条只说"配置错误"的启动失败，运维得翻源码才知道该填什么。"""
        monkeypatch.setenv("LLM_PROVIDER", "azure")
        with pytest.raises(ConfigError) as exc:
            load_settings()
        assert "internal" in str(exc.value) and "public" in str(exc.value)


class Test配置摘要:
    """启动日志与 `/readyz` 要能回答运维出事那一刻唯一想知道的事：
    **这台服务到底在跟谁说话。**"""

    def test_不含api_key(self) -> None:
        cfg = LLMSettings(
            provider="internal", base_url="https://x", api_key="SECRET-KEY",
            model="m", timeout_seconds=30, protocol="anthropic",
        )
        summary = cfg.summary()
        assert "SECRET-KEY" not in str(summary)
        assert "api_key" not in summary

    def test_说清provider与protocol与model(self) -> None:
        cfg = LLMSettings(
            provider="internal", base_url="https://x", api_key="k",
            model="claude-opus-5", timeout_seconds=30, protocol="anthropic",
        )
        assert cfg.summary() == {
            "provider": "internal", "protocol": "anthropic",
            "model": "claude-opus-5", "base_url": "https://x", "configured": True,
        }


class TestADR0007自动切换目标:
    """ADR-0007。`llm_fallback` 只是"有没有一个能切的目标"，
    什么时候真的切由 app/llm.py 的 FailoverLLMClient 判——这里只测装载。"""

    def test_provider是internal时装载公网配置(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "internal")
        fb = load_settings().llm_fallback
        assert fb is not None
        assert fb.provider == "public"
        assert fb.configured

    def test_provider已经是public时没有第三个方向可切(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "public")
        assert load_settings().llm_fallback is None

    def test_公网配置不全就当没有(self, monkeypatch) -> None:
        """半份配置比没配置更危险——真触发时才发现 fallback 也打不通，
        那时候用户已经在等一个永远不会来的回复。"""
        monkeypatch.setenv("LLM_PROVIDER", "internal")
        monkeypatch.setenv("PUBLIC_LLM_API_KEY", "")
        assert load_settings().llm_fallback is None

    def test_不设PUBLIC_LLM_PROTOCOL就落到openai(self, monkeypatch) -> None:
        """DeepSeek 是 OpenAI 兼容协议，这是 .env.example 那份默认值的来历。"""
        monkeypatch.setenv("LLM_PROVIDER", "internal")
        monkeypatch.delenv("PUBLIC_LLM_PROTOCOL", raising=False)
        assert load_settings().llm_fallback.protocol == "openai"


class Test限流配置:
    def test_默认不是不限(self, monkeypatch) -> None:
        """**默认要是 0（不限），限流就成了一个"需要记得打开"的功能**，
        而那种功能上线当天都是关着的。"""
        monkeypatch.delenv("RATE_LIMIT_START", raising=False)
        monkeypatch.delenv("RATE_LIMIT_TURN", raising=False)
        s = load_settings()
        assert s.rate_limit_start > 0
        assert s.rate_limit_turn > 0

    def test_可以关掉给本机调试用(self, monkeypatch) -> None:
        monkeypatch.setenv("RATE_LIMIT_TURN", "0")
        assert load_settings().rate_limit_turn == 0
