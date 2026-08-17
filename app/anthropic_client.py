"""大模型调用层（Anthropic Messages 协议）。

与 `app/llm.py` 是同一个端口的两个实现：probe / chat / stream 三个方法、
同样的入参形状（OpenAI 风格的 messages 列表）、同样抛 `LLMError`。
网关和编排层因此一行都不用改——切协议只改 `.env` 里的一个变量。

**为什么需要它**：公司内网网关（One API → Bedrock）上的 claude-opus-5
只认 Messages 协议，ChatCompletions 那条路会返回
`unsupported_relay_mode`。两套协议的差别不是换个 URL：

| | OpenAI ChatCompletions | Anthropic Messages |
|---|---|---|
| 系统提示词 | `messages` 里 role=system 的一条 | **顶层 `system` 参数**，放进 messages 会 400 |
| `max_tokens` | 选填 | **必填** |
| 流式增量 | `choices[0].delta.content` | `content_block_delta` 事件 |
| 采样参数 | temperature 常用 | **claude-opus-5 上已废弃，传了就 400** |
| 思考 | 无此概念 | **默认开着**，必须显式关 |

最后两条是实测出来的，不是从文档推的，每一条都有对应的注释写明后果。
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .config import LLMSettings, settings
from .llm import LLMError, Message

logger = logging.getLogger(__name__)

# `max_tokens` 在 Messages 协议里是必填的。给 512：老陈在微信上打字，
# 一轮最多三两句，五百多个 token 绰绰有余；同时它也是一道护栏——
# 模型偶尔会飘成大段旁白，与其让玩家等它写完，不如截断（安全层会丢掉半句）。
MAX_TOKENS = 512

# **思考必须显式关掉。** 这个模型默认开着思考，代价有两条，都是致命的：
# 一、实测 max_tokens=64 时全部预算被思考吃光，正文一个字都没有；
# 二、思考把首字延迟推到数秒，而演绎的 L1 降级线是 6 秒（app/engine.py）、
#     首句延迟指标是 P95 ≤ 3 秒（§9.4）。
# 这个作品要的是一个在微信上飞快回嘴的人，不是一个深思熟虑的助手。
THINKING_OFF: Dict[str, str] = {"type": "disabled"}

# 角色标签止损。模型偶尔会写完老陈的话之后接着往下编整段剧本——先吐一个
# 角色标签，再把玩家的台词也写出来。玩家于是看到老陈替自己发言（实测截图）。
#
# 根因已在 gateway 那边修掉（开场白不再当 assistant 消息，见 `_act_prompt`），
# 复发率从 4/4 降到 1/6；这一层是把剩下那 1/6 也堵死：一旦冒出标签，
# **后面的整段在服务端就不会生成**，省下的不只是那几个字，还有等它写完的时间。
#
# 为什么截到 `\nus` / `\nas` 而不是 `\nuser` / `\nassistant`：实测漏出来的形态
# 有 `user`、`assistant`、`usài`、`us` 加乱码——标签本身会被切坏。老陈在微信上
# 打的是中文，一行以两个拉丁字母开头只可能是标签，截了不会误伤。
ROLE_LEAK_STOPS = ["\nus", "\nas", "\nHuman", "\nAssistant", "\n##", "\n**"]


class AnthropicClient:
    """Anthropic Messages 协议的客户端，接口与 `LLMClient` 完全一致。"""

    def __init__(self, cfg: Optional[LLMSettings] = None) -> None:
        self.cfg = cfg or settings.llm
        self._client: Any = None
        self._loop: Any = None

    def _sdk(self) -> Any:
        """惰性建连。导入本模块不产生任何网络行为，与 LLMClient 一致。

        **连接池按事件循环缓存。** SDK 内部的 httpx 连接池绑在创建它的那个
        循环上，换一个循环再用就报 `Event loop is closed`（表面症状是
        `APIConnectionError`，很难往回查）。生产里 uvicorn 只有一个循环，
        碰不到；但 TestClient 每个请求换一个循环，第二轮必炸——
        旧的 LLMClient 每次调用现建 httpx 客户端，本来没有这个约束。

        连接留着不是省事，是省一次 TLS 握手：一局十二轮，每轮两个请求。
        """
        import asyncio

        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            from anthropic import AsyncAnthropic

            self._loop = loop
            self._client = AsyncAnthropic(
                api_key=self.cfg.api_key,
                base_url=self.cfg.base_url,
                timeout=self.cfg.timeout_seconds,
                # 重试交给上层：演绎超时要走 L1 降级（兜底台词），
                # SDK 在这儿默默重试会把 6 秒预算翻倍，降级反而不触发
                max_retries=0,
            )
        return self._client

    async def probe(self) -> Dict[str, Any]:
        """探测网关连通性。

        不走 `GET /models`——那是 OpenAI 的路子，Bedrock 后端上没有。
        改为发一条 max_tokens=1 的最小消息：花的钱可以忽略，但它验的是
        真正要用的那条链路（鉴权 + 模型可用 + 协议正确），比列模型有用。
        """
        if not self.cfg.configured:
            return {
                "ok": False,
                "provider": self.cfg.provider,
                "reason": "未配置 base_url / api_key / model",
            }
        try:
            await self._sdk().messages.create(
                model=self.cfg.model,
                max_tokens=1,
                thinking=THINKING_OFF,
                messages=[{"role": "user", "content": "hi"}],
            )
            return {
                "ok": True,
                "provider": self.cfg.provider,
                "protocol": "anthropic",
                "model": self.cfg.model,
                "base_url": self.cfg.base_url,
            }
        except Exception as exc:  # noqa: BLE001 - 网络不可达是预期结果之一
            return {
                "ok": False,
                "provider": self.cfg.provider,
                "protocol": "anthropic",
                "base_url": self.cfg.base_url,
                "reason": f"{type(exc).__name__}: {exc}",
            }

    async def chat(self, messages: List[Message], **kwargs: Any) -> str:
        if not self.cfg.configured:
            raise LLMError("大模型未配置，请检查 .env 中的 *_LLM_* 变量")

        system, turns = split_system(messages)
        try:
            resp = await self._sdk().messages.create(
                **self._payload(system, turns, kwargs)
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"调用大模型失败: {type(exc).__name__}: {exc}") from exc

        # content 是块的列表，可能混有非文本块。只取文本，其余一概忽略——
        # 判分与安全层吃的都是纯文本
        return "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )

    async def stream(
        self, messages: List[Message], **kwargs: Any
    ) -> AsyncIterator[str]:
        """流式输出。产出文本增量，与 `LLMClient.stream` 的产出形状一致。"""
        if not self.cfg.configured:
            raise LLMError("大模型未配置，请检查 .env 中的 *_LLM_* 变量")

        system, turns = split_system(messages)
        try:
            async with self._sdk().messages.stream(
                **self._payload(system, turns, kwargs)
            ) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"流式调用失败: {type(exc).__name__}: {exc}") from exc

    def _payload(
        self, system: str, turns: List[Message], kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": kwargs.get("max_tokens", MAX_TOKENS),
            "thinking": THINKING_OFF,
            "stop_sequences": ROLE_LEAK_STOPS,
            "messages": turns,
        }
        if system:
            payload["system"] = system
        # **temperature 被刻意丢掉**：claude-opus-5 上它已废弃，传任何值都返回
        # `temperature is deprecated for this model`。网关照旧传它（切回 DeepSeek
        # 时那条路还要用），由这一层负责丢——这正是适配层该干的事。
        return payload


def split_system(messages: List[Message]) -> Tuple[str, List[Message]]:
    """把 OpenAI 风格的消息列表拆成（系统提示词，对话轮次）。

    Messages 协议里 system 是顶层参数：留在 messages 里会被 400 拒绝
    （实测 `ValidationException`）。多条 system 按出现顺序拼接。
    """
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    turns = [m for m in messages if m.get("role") != "system"]
    return "\n\n".join(p for p in system_parts if p), turns
