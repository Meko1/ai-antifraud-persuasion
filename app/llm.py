"""大模型调用层（OpenAI 兼容协议）。

对外只有三个操作：probe / chat / stream。**stream 产出的是文本增量，不是原始
SSE 分块**——协议细节到此为止，上层不该知道 `choices[0].delta.content` 长什么样。
这一条在接入 Anthropic Messages 协议时才真正兑现：两种协议的事件结构毫无共同点，
但网关一行都不用改（见 app/anthropic_client.py）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .config import LLMSettings, settings

logger = logging.getLogger(__name__)

Message = Dict[str, str]

# ADR-0007。这句原文这个仓库自己在 2026-08-15、08-22、08-23 三次撞见过，
# 每次持续 8 小时以上、累计约 3400 次调用后出现。**不是任意 401**：
# 同一个网关的 `ip restriction!` 也是 401，但那个几分钟内自己恢复，
# 不该触发一个"往后都不再试内网"的永久切换。判据只认这一句原文。
QUOTA_EXHAUSTED_MARKER = "该令牌状态不可用"


class LLMError(RuntimeError):
    """调用大模型失败。调用方负责决定是降级还是向用户报错。

    `quota_exhausted` 由抛出方在构造时标记——两套协议实现（本文件的
    ChatCompletions、app/anthropic_client.py 的 Messages）各自的异常类型
    不同，统一在这里判一次文本，上层（FailoverLLMClient）不用关心协议。
    """

    def __init__(self, message: str, *, quota_exhausted: bool = False) -> None:
        super().__init__(message)
        self.quota_exhausted = quota_exhausted


def wrap_llm_error(prefix: str, exc: BaseException, *, body: str = "") -> LLMError:
    """把任意协议层抛出的异常包成 LLMError，顺手判一次是不是 token 用尽。

    判据是**原始错误文本**里有没有那句原文。两套协议给这句话的方式不一样：
    anthropic SDK 的异常 `str()` 直接就是网关的错误消息；httpx 的
    `HTTPStatusError.str()` 只有"401 Unauthorized"这种通用描述，**真正的
    错误消息在 `exc.response.text` 里，不传 body 参数会永远判不出来**——
    这是实现时踩到的一个坑，专门测试钉住了它（tests/test_llm_failover.py）。
    """
    text = f"{prefix}: {type(exc).__name__}: {exc}"
    haystack = f"{text} {body}" if body else text
    return LLMError(text, quota_exhausted=QUOTA_EXHAUSTED_MARKER in haystack)


class LLMClient:
    def __init__(self, cfg: Optional[LLMSettings] = None) -> None:
        self.cfg = cfg or settings.llm

    def status(self) -> Dict[str, Any]:
        """没套 FailoverLLMClient 时 `/healthz` 仍然想要这个字段——给一个
        "从来没切换过"的答案，省得那边为了"这个 provider 支不支持 status()"
        再分叉判断一次。

        `fallback` 为 None 的含义是**这个进程没有退路**：内网网关的 token
        一用尽，往后每一句都是兜底台词。这一位不能只在切换发生时才有——
        那时候再看见已经晚了八小时。
        """
        return {
            "active_provider": self.cfg.provider,
            # **模型名也要报**：`provider` 只说"内网还是公网"，回答不了
            # "这台服务到底在用 claude-opus-5 还是别的"——而那正是部署当天
            # 唯一想确认的一件事。
            "active_model": self.cfg.model,
            "switched": False,
            "switched_at": None, "reason": "", "fallback": None,
        }

    async def probe_fallback(self) -> Optional[Dict[str, Any]]:
        """没有 fallback 就没有可探的。返回 None，调用方据此不报这一栏。"""
        return None

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

    async def probe(self) -> Dict[str, Any]:
        """探测网关连通性。

        这是拿到服务器当天最先要跑的一件事：判断目标服务器所在的独立网段
        到底能不能访问内网模型网关。不通就把 LLM_PROVIDER 切成 public。
        """
        if not self.cfg.configured:
            return {
                "ok": False,
                "provider": self.cfg.provider,
                "reason": "未配置 base_url / api_key / model",
            }

        try:
            async with httpx.AsyncClient(timeout=min(self.cfg.timeout_seconds, 10)) as client:
                resp = await client.get(f"{self.cfg.base_url}/models", headers=self._headers())
            return {
                "ok": resp.status_code < 400,
                "provider": self.cfg.provider,
                "status_code": resp.status_code,
                "base_url": self.cfg.base_url,
            }
        except httpx.HTTPError as exc:
            # 网络不可达在这里是预期结果之一，不是 bug，如实返回原因
            return {
                "ok": False,
                "provider": self.cfg.provider,
                "base_url": self.cfg.base_url,
                "reason": f"{type(exc).__name__}: {exc}",
            }

    async def chat(self, messages: List[Message], **kwargs: Any) -> str:
        if not self.cfg.configured:
            raise LLMError("大模型未配置，请检查 .env 中的 *_LLM_* 变量")

        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.8),
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.cfg.timeout_seconds) as client:
                resp = await client.post(
                    f"{self.cfg.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # 真正的网关错误消息在这儿，不在 str(exc) 里——见 wrap_llm_error 的注
            raise wrap_llm_error("调用大模型失败", exc, body=exc.response.text) from exc
        except httpx.HTTPError as exc:
            raise wrap_llm_error("调用大模型失败", exc) from exc

        # 200 但不是 JSON，是**地址填错**最常见的样子：One API 那台网关的
        # ChatCompletions 在 `/coding/v1` 下，填成 `/coding` 会打到前端页面上，
        # 于是拿回一整页 HTML 和 200。裸的 JSONDecodeError 一路冒到调用方，
        # 跑批那边只会印一行 `JSONDecodeError`——查不出是网关抽风还是路径写错。
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(
                f"大模型返回的不是 JSON（HTTP {resp.status_code}，"
                f"很可能 base_url 少了 /v1）: {resp.text[:120]!r}"
            ) from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"大模型返回结构异常: {data!r}") from exc

    async def stream(self, messages: List[Message], **kwargs: Any) -> AsyncIterator[str]:
        """流式输出。

        投票日几百人并发时，感知延迟由它决定：让字一个个蹦出来，
        用户就不会盯着白屏数秒然后关掉页面。
        """
        if not self.cfg.configured:
            raise LLMError("大模型未配置，请检查 .env 中的 *_LLM_* 变量")

        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.8),
            "stream": True,
        }
        try:
            async with httpx.AsyncClient(timeout=self.cfg.timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    f"{self.cfg.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if not chunk or chunk == "[DONE]":
                            continue
                        delta = _extract_delta(chunk)
                        if delta:
                            yield delta
        except httpx.HTTPStatusError as exc:
            # 流式响应的正文在失败这一刻可能还没被读进来（httpx 的
            # `client.stream()` 是惰性的），不显式 aread() 就去碰 `.text`
            # 会抛 ResponseNotRead——读不到就算了，判据退回只看 str(exc)
            body = ""
            try:
                await exc.response.aread()
                body = exc.response.text
            except Exception:  # noqa: BLE001 - 读原文失败不该让这一轮的报错也跟着炸
                pass
            raise wrap_llm_error("流式调用失败", exc, body=body) from exc
        except httpx.HTTPError as exc:
            raise wrap_llm_error("流式调用失败", exc) from exc


def _extract_delta(raw: str) -> str:
    """从一块流式响应里取出文本增量。结构异常一律当成空块跳过。"""
    try:
        payload = json.loads(raw)
        return payload["choices"][0]["delta"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


class FailoverLLMClient:
    """ADR-0007：内网网关 token 用尽时，自动切到运维已配置的公网模型。

    对 `app/gateway.py` 而言这是个透明包装——`chat` / `stream` / `probe`
    三个方法签名与底下两种客户端完全一致，切不切换、切给谁，编排层一行都
    不用知道。

    **切换只发生一次方向，且不会自动切回。** 见 ADR-0007「明确不做的」
    那一节：判定"内网好了"比判定"内网坏了"难得多，这次只解决"扛住一整个
    工作日的中断"，不做自动恢复——要切回内网，重启服务。
    """

    def __init__(self, primary: Any, fallback: Any) -> None:
        self._primary = primary
        self._fallback = fallback
        self._active = primary
        self._switched_at: Optional[float] = None
        self._switch_reason: str = ""

    def status(self) -> Dict[str, Any]:
        """`/healthz` 用这个回答"这个进程现在在跟谁说话、是不是自动切过的"。

        ADR-0005 反对自动切换的核心理由是"无人知情"——这个方法就是补上
        那只眼睛的地方，不能省。
        """
        return {
            "active_provider": self._active.cfg.provider,
            # 切换之后这一位跟着变。**看这一位，不要看启动日志里那个模型名**——
            # 切过之后启动日志说的仍然是 claude-opus-5，而实际在答的是 DeepSeek。
            "active_model": self._active.cfg.model,
            "switched": self._active is self._fallback,
            "switched_at": self._switched_at,
            "reason": self._switch_reason,
            # **退路本身也要能被看见，而且要在用上它之前**。只报"切没切过"
            # 回答不了运维在部署当天真正要问的那个问题：token 明天用尽的话，
            # 这台机器接得住吗。接不住的样子是安静的——每一句都变成兜底台词，
            # /healthz 照样 ok
            "fallback": {
                "provider": self._fallback.cfg.provider,
                "model": self._fallback.cfg.model,
                "protocol": self._fallback.cfg.protocol,
            },
        }

    def _record_switch(self, exc: LLMError) -> None:
        """记一次切换。**这个方法不决定"这次请求要不要重试"**，那是
        `chat`/`stream` 自己的事——原因见下面那个真实撞见的坑。

        `app/engine.py` 的 `play_turn` 用 `asyncio.create_task` 并发跑
        `act()`（这个类的 `stream`）与 `classify()`（这个类的 `chat`），
        两边会在同一轮里**几乎同时**打到同一个已耗尽的内网网关。
        如果"要不要重试"由"是不是我把 `_active` 切过去的"来判断，
        后到的那一个会看见 `_active` 已经是 fallback、以为"这不是我的事"，
        直接把异常扔上去——**实测复现过**：`classify()` 切换成功、
        `act()` 却仍然走了 L1 兜底台词，回复看着像复读机。
        判据因此改成"这次调用本身发没发生在切换之前"（`chat`/`stream`
        各自记录的 `was_primary`），不看 `_active` 这个共享状态的瞬时值。
        """
        if self._active is self._fallback:
            return  # 已经有人切过了，这里只负责别重复打日志
        self._active = self._fallback
        self._switched_at = time.time()
        self._switch_reason = str(exc)
        logger.error(
            "内网网关 token 用尽，自动切到 %s（%s）。这个进程往后都会走这条路，"
            "要切回内网请修好网关后重启服务。原始报错：%s",
            self._fallback.cfg.provider, self._fallback.cfg.model, exc,
        )

    async def probe(self) -> Dict[str, Any]:
        """探的是**当前生效**的那一个，不是永远探主 provider——
        已经切换之后再报"内网探测失败"对运维没有信息量，他们已经知道了。"""
        return await self._active.probe()

    async def probe_fallback(self) -> Dict[str, Any]:
        """单独探一次退路。

        **这是这条降级链路唯一能在需要它之前被验证的时刻。** ADR-0007 的
        触发条件是内网网关回那句"该令牌状态不可用"，而那一刻通常是某天
        下午——如果公网凭证填错或者账号欠费，切换会"成功"然后立刻再失败，
        最终落回兜底台词，比不切还难查。部署当天探一次，这个失败面就没了。
        """
        return await self._fallback.probe()

    async def chat(self, messages: List[Message], **kwargs: Any) -> str:
        called_primary = self._active is self._primary
        try:
            return await self._active.chat(messages, **kwargs)
        except LLMError as exc:
            if called_primary and exc.quota_exhausted:
                self._record_switch(exc)
                return await self._fallback.chat(messages, **kwargs)
            raise

    async def stream(self, messages: List[Message], **kwargs: Any) -> AsyncIterator[str]:
        called_primary = self._active is self._primary
        # 已经吐出过增量之后再切换，会把"半句主 provider 的话"接上
        # "整段公网重来一遍"，拼出一句没人说过的话。**只在一个字都没吐出去
        # 之前才允许换台重播**——鉴权失败本来就发生在第一个字节之前，
        # 这条限制因此不会漏掉真实要接的那种失败。
        spoken = False
        try:
            async for delta in self._active.stream(messages, **kwargs):
                spoken = True
                yield delta
            return
        except LLMError as exc:
            if spoken or not (called_primary and exc.quota_exhausted):
                raise
            self._record_switch(exc)
        async for delta in self._fallback.stream(messages, **kwargs):
            yield delta


def _build_single(cfg: LLMSettings) -> Any:
    """按协议选客户端。两种协议共用 probe / chat / stream 三个方法。

    延迟导入 anthropic：走 OpenAI 协议的部署（公网 DeepSeek 那条路）不该被
    一个用不上的依赖卡住启动。
    """
    if cfg.protocol == "anthropic":
        from .anthropic_client import AnthropicClient

        return AnthropicClient(cfg)
    return LLMClient(cfg)


def build_client(cfg: Optional[LLMSettings] = None) -> Any:
    """构造对外的那一个客户端。

    只有走默认参数（即 `settings.llm`，模块加载时的那一次调用）才会按
    ADR-0007 套上 `FailoverLLMClient`：传自定义 `cfg` 的调用方（目前没有，
    但接口留着）拿到的是单个客户端，不多一层它没要求过的行为。
    """
    use_default = cfg is None
    cfg = cfg or settings.llm
    primary = _build_single(cfg)
    if use_default and settings.llm_fallback is not None:
        fallback = _build_single(settings.llm_fallback)
        return FailoverLLMClient(primary, fallback)
    return primary


llm_client = build_client()
