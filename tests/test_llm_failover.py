"""ADR-0007：内网网关 token 用尽时自动切到公网模型。

这一份钉两件事：
  1. `wrap_llm_error` 的判据不会把"网关随便一个 401"都当成 token 用尽
     （`ip restriction!` 那种几分钟自愈的 401 必须被放过）。
  2. `FailoverLLMClient` 的切换时机与"不切回"这两条行为规则。

**唯一被替身的是网关**（与 test_engine.py 同一条 mocking.md 红线）：
`FailoverLLMClient` 本身、`wrap_llm_error`、`LLMError.quota_exhausted`
全部用真货。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List

import httpx
import pytest

from app.llm import (
    QUOTA_EXHAUSTED_MARKER,
    FailoverLLMClient,
    LLMClient,
    LLMError,
    wrap_llm_error,
)


class Test判据只认那句原文:
    def test_命中那句话就是token用尽(self) -> None:
        exc = wrap_llm_error("调用失败", RuntimeError("401 该令牌状态不可用"))
        assert exc.quota_exhausted is True

    def test_ip限流不是token用尽(self) -> None:
        """这是同一个网关的另一种 401，历史上几分钟内自己恢复
        （docs/HANDOFF.md）。拿它去触发一个永久切换，代价是把一次
        瞬时抖动升级成一整天走公网。"""
        exc = wrap_llm_error("调用失败", RuntimeError("401 ip restriction!"))
        assert exc.quota_exhausted is False

    def test_body参数里的信号也算数(self) -> None:
        """httpx 的 HTTPStatusError.str() 不含响应体，真正的错误消息
        要靠 body 参数传进来——这是实现时真的踩过的坑。"""
        exc = wrap_llm_error("调用失败", RuntimeError("401 Unauthorized"),
                              body='{"error":{"message":"该令牌状态不可用"}}')
        assert exc.quota_exhausted is True

    def test_并发撞上RPM上限算降级信号(self) -> None:
        """30 RPM 是这个令牌对 claude-sonnet-5 的真实上限，`tools/act_eval.py`
        并发 4 跑批时当场撞到过。展示当天三四位评委同时玩就会复现，而那一刻
        不降级的话，玩家拿到的每一句都是兜底罐头台词。

        **它不是 token 用尽**（那边额度归零不会自愈，这边一分钟后自愈），
        所以 `quota_exhausted` 仍然为假；共用的只是"切到退路"这条处置。
        """
        exc = wrap_llm_error(
            "流式调用失败",
            RuntimeError("RateLimitError: Error code: 429 - 该令牌对模型 "
                         "claude-sonnet-5 的RPM已经到达上限，当前值 31，RPM限制 30"),
        )
        assert exc.rate_limited
        assert not exc.quota_exhausted
        assert exc.failover_worthy

    def test_普通网络错误既不是用尽也不是限流(self) -> None:
        """两条判据都不许把随便一个超时算进去——那会让一次网络抖动
        把整个进程永久钉在公网退路上。"""
        exc = wrap_llm_error("调用失败", TimeoutError("timed out"))
        assert not exc.failover_worthy

    def test_普通网络错误不是token用尽(self) -> None:
        exc = wrap_llm_error("调用失败", TimeoutError("timed out"))
        assert exc.quota_exhausted is False


def _cfg(**overrides):
    from app.config import LLMSettings

    base = dict(
        provider="internal", base_url="https://gateway.invalid", api_key="k",
        model="m", timeout_seconds=5, protocol="openai",
    )
    base.update(overrides)
    return LLMSettings(**base)


class TestLLMClient真实HTTP路径:
    """走真的 httpx.AsyncClient，只在传输层换成 MockTransport——
    这样 `resp.raise_for_status()` / `exc.response.text` 这条真实链路
    被覆盖到，而不是只测封装函数本身。"""

    def test_401带那句原文时quota_exhausted为真(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": QUOTA_EXHAUSTED_MARKER}})

        _patch_async_client(monkeypatch, handler)
        client = LLMClient(_cfg())

        with pytest.raises(LLMError) as exc_info:
            asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
        assert exc_info.value.quota_exhausted is True

    def test_401带ip限流时quota_exhausted为假(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "ip restriction!"}})

        _patch_async_client(monkeypatch, handler)
        client = LLMClient(_cfg())

        with pytest.raises(LLMError) as exc_info:
            asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
        assert exc_info.value.quota_exhausted is False

    def test_流式响应里的401也能判出来(self, monkeypatch) -> None:
        """这条钉的是 aread() 那个修复：流式响应的正文在失败那一刻
        可能还没被读进来，不显式读一次会拿到空字符串，永远判不出来。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": QUOTA_EXHAUSTED_MARKER}})

        _patch_async_client(monkeypatch, handler)
        client = LLMClient(_cfg())

        async def drain():
            out = []
            async for delta in client.stream([{"role": "user", "content": "hi"}]):
                out.append(delta)
            return out

        with pytest.raises(LLMError) as exc_info:
            asyncio.run(drain())
        assert exc_info.value.quota_exhausted is True


def _patch_async_client(monkeypatch, handler) -> None:
    """把 httpx.AsyncClient 的传输层换成 MockTransport，不碰真网络。"""
    import httpx as _httpx

    real_init = _httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = _httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(_httpx.AsyncClient, "__init__", patched_init)


class FakeProviderClient:
    """FailoverLLMClient 的下位替身。只实现它需要的三个方法。"""

    def __init__(self, provider: str, model: str = "m", protocol: str = "openai") -> None:
        class _Cfg:
            pass
        self.cfg = _Cfg()
        self.cfg.provider = provider
        self.cfg.model = model
        # status() 要把退路是谁报出来，protocol 是那三栏之一
        self.cfg.protocol = protocol
        self.probe_calls = 0
        self.chat_calls: List[List[Dict[str, str]]] = []
        self.stream_calls = 0
        self._chat_result: Any = "ok"
        self._chat_error: LLMError | None = None
        self._stream_chunks: List[str] = ["a", "b"]
        self._stream_error: LLMError | None = None
        self._stream_fail_after: int = 0  # 吐几个字之后才报错
        self._chat_delay: float = 0  # 制造两次并发调用真正交错的窗口

    async def chat(self, messages, **kwargs) -> str:
        self.chat_calls.append(messages)
        if self._chat_delay:
            await asyncio.sleep(self._chat_delay)
        if self._chat_error is not None:
            raise self._chat_error
        return self._chat_result

    async def stream(self, messages, **kwargs) -> AsyncIterator[str]:
        self.stream_calls += 1
        for i, chunk in enumerate(self._stream_chunks):
            if self._stream_error is not None and i == self._stream_fail_after:
                raise self._stream_error
            yield chunk

    async def probe(self) -> Dict[str, Any]:
        self.probe_calls += 1
        return {"ok": True, "provider": self.cfg.provider}


class Test退路本身要能被看见:
    """ADR-0007 的降级链路，在**用上它之前**就该能被验证。

    这几条守的是同一件事：内网 token 用尽那天（本仓库撞过三次，每次持续
    一整个工作日）这台机器接不接得住。接不住的样子是安静的——/healthz
    一路 ok，只是每一句台词都变成兜底台词。
    """

    def test_status要报出退路是谁(self) -> None:
        router = FailoverLLMClient(
            FakeProviderClient("internal"),
            FakeProviderClient("public", model="deepseek-v4-pro"),
        )

        fallback = router.status()["fallback"]

        assert fallback == {
            "provider": "public", "model": "deepseek-v4-pro", "protocol": "openai",
        }

    def test_没套failover时fallback是None_那是没有退路(self) -> None:
        # 这一位不能省：它是"这个进程没有退路"唯一的对外说法
        from app.config import LLMSettings

        client = LLMClient(LLMSettings(
            provider="internal", base_url="http://x/v1", api_key="k",
            model="m", timeout_seconds=1, protocol="openai",
        ))

        assert client.status()["fallback"] is None

    def test_probe_fallback探的是退路那一个_不是当前生效那一个(self) -> None:
        primary = FakeProviderClient("internal")
        fallback = FakeProviderClient("public")
        router = FailoverLLMClient(primary, fallback)

        result = asyncio.run(router.probe_fallback())

        assert result["provider"] == "public"
        assert fallback.probe_calls == 1
        assert primary.probe_calls == 0  # 探退路不该顺手打一次主网关

    def test_没有退路时probe_fallback返回None(self) -> None:
        from app.config import LLMSettings

        client = LLMClient(LLMSettings(
            provider="internal", base_url="http://x/v1", api_key="k",
            model="m", timeout_seconds=1, protocol="openai",
        ))

        assert asyncio.run(client.probe_fallback()) is None


class Test自动切换_chat:
    def test_主provider正常时不碰fallback(self) -> None:
        primary = FakeProviderClient("internal")
        fallback = FakeProviderClient("public")
        router = FailoverLLMClient(primary, fallback)

        result = asyncio.run(router.chat([{"role": "user", "content": "hi"}]))

        assert result == "ok"
        assert fallback.chat_calls == []
        assert router.status()["switched"] is False

    def test_token用尽时当次请求重试到公网(self) -> None:
        primary = FakeProviderClient("internal")
        primary._chat_error = wrap_llm_error("x", RuntimeError(QUOTA_EXHAUSTED_MARKER))
        fallback = FakeProviderClient("public")
        fallback._chat_result = "公网回的话"
        router = FailoverLLMClient(primary, fallback)

        result = asyncio.run(router.chat([{"role": "user", "content": "hi"}]))

        assert result == "公网回的话"
        assert len(fallback.chat_calls) == 1
        status = router.status()
        assert status["switched"] is True
        assert status["active_provider"] == "public"
        assert status["switched_at"] is not None
        assert QUOTA_EXHAUSTED_MARKER in status["reason"]

    def test_非token用尽的错误不触发切换(self) -> None:
        """网络抖一下不该把往后一整天的流量都送去公网——那是 L1/L2
        降级该接的，不是这一层的事。"""
        primary = FakeProviderClient("internal")
        primary._chat_error = wrap_llm_error("x", TimeoutError("timed out"))
        fallback = FakeProviderClient("public")
        router = FailoverLLMClient(primary, fallback)

        with pytest.raises(LLMError):
            asyncio.run(router.chat([{"role": "user", "content": "hi"}]))

        assert fallback.chat_calls == []
        assert router.status()["switched"] is False

    def test_切过一次之后不再试主provider(self) -> None:
        """token 用尽历史上持续过 8 小时以上，每轮都去试一个已知报废的
        网关只会让每个用户多等一次超时。"""
        primary = FakeProviderClient("internal")
        primary._chat_error = wrap_llm_error("x", RuntimeError(QUOTA_EXHAUSTED_MARKER))
        fallback = FakeProviderClient("public")
        fallback._chat_result = "公网"
        router = FailoverLLMClient(primary, fallback)

        asyncio.run(router.chat([{"role": "user", "content": "第一轮"}]))
        # 之后 primary 就算恢复正常也不该被再摸一下
        primary._chat_error = None
        asyncio.run(router.chat([{"role": "user", "content": "第二轮"}]))

        assert len(primary.chat_calls) == 1  # 只有触发切换的那一次
        assert len(fallback.chat_calls) == 2

    def test_两个并发调用同时撞上耗尽_两个都要重试成功(self) -> None:
        """这是真在部署机上撞见的 bug，不是猜出来的场景：`app/engine.py`
        的 `play_turn` 用 `asyncio.create_task` 并发跑 `act()`（stream）
        与 `classify()`（chat）,两边几乎同时打到同一个耗尽的网关。

        第一版的判据是"是不是我把 _active 切过去的"——后到的那一个看见
        `_active` 已经被先到的那个切成 fallback，就以为"这不是我的事"，
        直接把异常扔了上去。实测复现：`classify()` 切换成功，`act()`
        却仍然走了 L1 兜底台词，回复看着像复读机。这条测试钉住修复：
        **两次独立的调用都必须各自重试成功**，不能只有先到的那个。
        """
        primary = FakeProviderClient("internal")
        primary._chat_error = wrap_llm_error("x", RuntimeError(QUOTA_EXHAUSTED_MARKER))
        primary._chat_delay = 0.02  # 制造真正的交错，不靠事件循环凑巧顺序对
        fallback = FakeProviderClient("public")
        fallback._chat_result = "公网"
        router = FailoverLLMClient(primary, fallback)

        async def run():
            return await asyncio.gather(
                router.chat([{"role": "user", "content": "act"}]),
                router.chat([{"role": "user", "content": "classify"}]),
            )

        results = asyncio.run(run())

        assert results == ["公网", "公网"], (
            "两个并发请求都命中了 token 用尽，理应都拿到公网的回复；"
            f"实际拿到 {results}——有一个被漏判成'已经是别人的事了'"
        )
        assert len(primary.chat_calls) == 2  # 两边都真的先摸了一次内网
        assert len(fallback.chat_calls) == 2  # 两边都成功重试到了公网


class Test自动切换_stream:
    def test_一个字都没吐出去时可以换台重播(self) -> None:
        primary = FakeProviderClient("internal")
        primary._stream_error = wrap_llm_error("x", RuntimeError(QUOTA_EXHAUSTED_MARKER))
        primary._stream_fail_after = 0  # 第一个字之前就炸
        fallback = FakeProviderClient("public")
        fallback._stream_chunks = ["公", "网", "的", "话"]
        router = FailoverLLMClient(primary, fallback)

        out = asyncio.run(_drain(router.stream([{"role": "user", "content": "hi"}])))

        assert out == ["公", "网", "的", "话"]
        assert router.status()["switched"] is True

    def test_已经吐过字之后失败_不换台_直接往上抛(self) -> None:
        """半句主 provider 的话接上整段公网重来一遍，会拼出一句
        没人说过的话——这种情况交给上层现有的 L1 兜底，不在这儿硬接。"""
        primary = FakeProviderClient("internal")
        primary._stream_chunks = ["先", "说", "一半"]
        primary._stream_error = wrap_llm_error("x", RuntimeError(QUOTA_EXHAUSTED_MARKER))
        primary._stream_fail_after = 2  # 吐了两个字之后才炸
        fallback = FakeProviderClient("public")
        router = FailoverLLMClient(primary, fallback)

        collected: List[str] = []
        with pytest.raises(LLMError):
            asyncio.run(_drain_into(router.stream([{"role": "user", "content": "hi"}]), collected))

        assert collected == ["先", "说"]
        assert fallback.stream_calls == 0
        assert router.status()["switched"] is False


async def _drain(agen: AsyncIterator[str]) -> List[str]:
    return [x async for x in agen]


async def _drain_into(agen: AsyncIterator[str], out: List[str]) -> None:
    async for x in agen:
        out.append(x)
