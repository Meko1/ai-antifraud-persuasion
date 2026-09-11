"""[DEBUG-c0nc] 并发下「暂时无法接入客户」的复现环。

用法：
    python dbg_concurrency.py                       # 默认 40 并发
    python dbg_concurrency.py --users 40 --xff      # 每个用户一个独立 IP
    python dbg_concurrency.py --users 40 --no-xff   # 全部挤在同一个桶里

判据只有一条：**有没有用户在 `POST /api/game/start` 上拿到非 200**。
那一下就是前端 opening.js 打出「暂时无法接入客户。」的时刻。

上游用假客户端模拟，不打真网关：
  --upstream-capacity  网关同时能服务几个请求，超了就排队（模拟拥塞）
  --upstream-latency   单个请求的耗时
  --upstream-rpm       每分钟额度，超了立刻 429（模拟 RPM 闸；0=不限）
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections import Counter
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List

os.environ.setdefault("STATE_SIGNING_SECRET", "dbg-concurrency-not-a-real-key")
os.environ.setdefault("REDIS_URL", "")

import httpx  # noqa: E402

from app.gateway import ModelGateway  # noqa: E402
from app.guard import breaker, guard  # noqa: E402
from app.llm import LLMError  # noqa: E402
from app.main import app  # noqa: E402
from app.routes.game import get_gateway  # noqa: E402


class CongestedClient:
    """一台会拥塞的模型网关。

    两种压法各自对应真实见过的一种：
      · capacity + latency —— 网关排队（One API 那台在并发上去之后就是这样）
      · rpm               —— 额度闸，超了立刻 429
    """

    def __init__(self, *, capacity: int, latency: float, rpm: int) -> None:
        self._sem = asyncio.Semaphore(capacity)
        self._latency = latency
        self._rpm = rpm
        self._used = 0
        self.calls = 0
        self.rejected = 0
        self.cfg = SimpleNamespace(
            provider="internal", model="fake-internal", protocol="anthropic"
        )

    async def _gate(self) -> None:
        self.calls += 1
        if self._rpm:
            self._used += 1
            if self._used > self._rpm:
                self.rejected += 1
                raise LLMError(
                    "流式调用失败: HTTPStatusError: 429 Too Many Requests",
                    rate_limited=True,
                )
        await asyncio.sleep(self._latency)

    async def chat(self, messages: List[Dict[str, str]], **_: Any) -> str:
        async with self._sem:
            await self._gate()
        return '{"hit_keys": ["socratic_question"], "grounded": true, "evidence": "王老师"}'

    async def stream(self, messages: List[Dict[str, str]], **_: Any) -> AsyncIterator[str]:
        async with self._sem:
            await self._gate()
            for ch in "别劝我 老师说了今天最后一天":
                yield ch


async def one_user(
    client: httpx.AsyncClient, idx: int, *, xff: bool, rounds: int
) -> Dict[str, Any]:
    headers = {"X-Forwarded-For": f"10.0.{idx // 256}.{idx % 256}"} if xff else {}
    out: Dict[str, Any] = {"start": 0, "turns": []}

    resp = await client.post("/api/game/start", json={}, headers=headers)
    out["start"] = resp.status_code
    if resp.status_code != 200:
        out["start_body"] = resp.text[:120]
        return out
    token = resp.json()["token"]

    for _ in range(rounds):
        r = await client.post(
            "/api/game/turn",
            json={"token": token, "utterance": "这笔钱本来是准备做什么用的？"},
            headers=headers,
        )
        body = r.text
        out["turns"].append(r.status_code)
        for block in body.strip().split("\n\n"):
            if "event: meta" in block or '"token"' in block:
                import json as _json

                for line in block.splitlines():
                    if line.startswith("data:"):
                        data = _json.loads(line[5:].strip())
                        if data.get("token"):
                            token = data["token"]
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=40)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--ramp", type=float, default=0.0, help="用户进场铺开的秒数")
    ap.add_argument("--upstream-capacity", type=int, default=4)
    ap.add_argument("--upstream-latency", type=float, default=1.0)
    ap.add_argument("--upstream-rpm", type=int, default=0)
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--xff", dest="xff", action="store_true", default=True)
    grp.add_argument("--no-xff", dest="xff", action="store_false")
    args = ap.parse_args()

    guard.reset()
    breaker.reset()

    upstream = CongestedClient(
        capacity=args.upstream_capacity,
        latency=args.upstream_latency,
        rpm=args.upstream_rpm,
    )
    app.dependency_overrides[get_gateway] = lambda: ModelGateway(client=upstream)

    transport = httpx.ASGITransport(app=app)
    t0 = time.monotonic()
    async with httpx.AsyncClient(
        transport=transport, base_url="http://dbg", timeout=120
    ) as client:

        async def staggered(i: int) -> Dict[str, Any]:
            if args.ramp:
                await asyncio.sleep(args.ramp * i / max(args.users, 1))
            return await one_user(client, i, xff=args.xff, rounds=args.rounds)

        results = await asyncio.gather(*(staggered(i) for i in range(args.users)))
    elapsed = time.monotonic() - t0
    app.dependency_overrides.pop(get_gateway, None)

    starts = Counter(r["start"] for r in results)
    failed = [r for r in results if r["start"] != 200]
    print(f"并发 {args.users} · {args.rounds} 轮 · 耗时 {elapsed:.1f}s")
    print(f"上游：capacity={args.upstream_capacity} latency={args.upstream_latency}s "
          f"rpm={args.upstream_rpm or '不限'} 调用 {upstream.calls} 次 "
          f"拒绝 {upstream.rejected} 次")
    print(f"/api/game/start 状态码分布：{dict(starts)}")
    if failed:
        print(f"样例失败响应：{failed[0].get('start_body', '')!r}")
    print(f"熔断器：consecutive={breaker._consecutive} open={breaker.open}")
    if failed:
        print(f"\nFAIL：{len(failed)}/{args.users} 位用户看到「暂时无法接入客户」")
        return 1
    print(f"\nPASS：{args.users} 位用户全部接入成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
