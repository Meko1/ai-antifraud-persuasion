"""大模型调用层（OpenAI 兼容协议）。

对外只有三个操作：probe / chat / stream。**stream 产出的是文本增量，不是原始
SSE 分块**——协议细节到此为止，上层不该知道 `choices[0].delta.content` 长什么样。
这一条在接入 Anthropic Messages 协议时才真正兑现：两种协议的事件结构毫无共同点，
但网关一行都不用改（见 app/anthropic_client.py）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .config import LLMSettings, settings

logger = logging.getLogger(__name__)

Message = Dict[str, str]


class LLMError(RuntimeError):
    """调用大模型失败。调用方负责决定是降级还是向用户报错。"""


class LLMClient:
    def __init__(self, cfg: Optional[LLMSettings] = None) -> None:
        self.cfg = cfg or settings.llm

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
                data = resp.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"调用大模型失败: {type(exc).__name__}: {exc}") from exc

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
        except httpx.HTTPError as exc:
            raise LLMError(f"流式调用失败: {type(exc).__name__}: {exc}") from exc


def _extract_delta(raw: str) -> str:
    """从一块流式响应里取出文本增量。结构异常一律当成空块跳过。"""
    try:
        payload = json.loads(raw)
        return payload["choices"][0]["delta"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def build_client(cfg: Optional[LLMSettings] = None) -> Any:
    """按协议选客户端。两种协议共用 probe / chat / stream 三个方法。

    延迟导入 anthropic：走 OpenAI 协议的部署（公网 DeepSeek 那条路）不该被
    一个用不上的依赖卡住启动。ADR-0005 说不做自动故障切换——协议同理，
    切换是一次显式的配置动作，不是运行时的猜测。
    """
    cfg = cfg or settings.llm
    if cfg.protocol == "anthropic":
        from .anthropic_client import AnthropicClient

        return AnthropicClient(cfg)
    return LLMClient(cfg)


llm_client = build_client()
