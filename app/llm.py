"""大模型调用层（OpenAI 兼容协议）。

这里只提供最小可用的 probe / chat / stream。真正的对局逻辑（结构化 JSON 输出、
状态机判分、输出安全层）在后续迭代中接入，接口形状先固定下来。
"""

from __future__ import annotations

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
                        yield chunk
        except httpx.HTTPError as exc:
            raise LLMError(f"流式调用失败: {type(exc).__name__}: {exc}") from exc


llm_client = LLMClient()
