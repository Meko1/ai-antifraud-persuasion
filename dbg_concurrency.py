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


async def scenario(
    name: str,
    *,
    users: int,
    rounds: int,
    ramp: float,
    xff: bool,
    capacity: int,
    latency: float,
    rpm: int,
) -> bool:
    guard.reset()
    breaker.reset()
    upstream = CongestedClient(capacity=capacity, latency=latency, rpm=rpm)
    app.dependency_overrides[get_gateway] = lambda: ModelGateway(client=upstream)

    t0 = time.monotonic()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://dbg", timeout=120
    ) as client:

        async def staggered(i: int) -> Dict[str, Any]:
            if ramp:
                await asyncio.sleep(ramp * i / max(users, 1))
            return await one_user(client, i, xff=xff, rounds=rounds)

        results = await asyncio.gather(*(staggered(i) for i in range(users)))
    elapsed = time.monotonic() - t0
    app.dependency_overrides.pop(get_gateway, None)

    starts = Counter(r["start"] for r in results)
    failed = [r for r in results if r["start"] != 200]
    verdict = "FAIL" if failed else "PASS"
    print(f"[{verdict}] {name}")
    print(f"        {users} 人 · {rounds} 轮 · {elapsed:.1f}s · "
          f"上游 cap={capacity} lat={latency}s rpm={rpm or '不限'} "
          f"（调用 {upstream.calls}，拒绝 {upstream.rejected}）")
    print(f"        /api/game/start → {dict(starts)}"
          f"   熔断 open={breaker.open}")
    if failed:
        print(f"        {len(failed)}/{users} 位用户看到「暂时无法接入客户」："
              f"{failed[0].get('start_body', '')!r}")
    return not failed


SCENARIOS = (
    dict(name="A 对照：上游健康、每人独立 IP",
         users=40, rounds=1, ramp=0.0, xff=True,
         capacity=16, latency=0.05, rpm=0),
    dict(name="B 反代不透传真实 IP（所有人共用一个限流桶）",
         users=40, rounds=1, ramp=0.0, xff=False,
         capacity=16, latency=0.05, rpm=0),
    dict(name="C 上游撞额度，后到的人被熔断挡在门外",
         users=24, rounds=2, ramp=4.0, xff=True,
         capacity=8, latency=0.05, rpm=12),
)


async def scenario_latch() -> bool:
    """D 上游已经恢复，但熔断器自己出不来。

    熔断器只有 `record(ok=True)` 能合上，而那一行只在**一轮对局**里被调到。
    张开之后新对局一律 503 → 没有新对局 → 没有人能给它一次成功 → 永远张着。
    在场的那几局打完、人走干净，这台服务就再也接不进任何人，直到重启。
    """
    guard.reset()
    breaker.reset()

    dead = CongestedClient(capacity=8, latency=0.0, rpm=1)   # 第 2 次调用起全 429
    healthy = CongestedClient(capacity=16, latency=0.0, rpm=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://dbg", timeout=120
    ) as client:
        # 1. 上游挂着，几个人在打——把熔断器顶开
        app.dependency_overrides[get_gateway] = lambda: ModelGateway(client=dead)
        await asyncio.gather(
            *(one_user(client, i, xff=True, rounds=2) for i in range(6))
        )
        tripped = breaker.open

        # 2. 上游完全恢复，现场一个人都不剩
        app.dependency_overrides[get_gateway] = lambda: ModelGateway(client=healthy)
        await asyncio.sleep(1.0)

        # 3. 新人来了。上游是好的，他该进得去
        codes = [
            (await client.post(
                "/api/game/start", json={},
                headers={"X-Forwarded-For": f"10.9.0.{i}"},
            )).status_code
            for i in range(3)
        ]

    app.dependency_overrides.pop(get_gateway, None)
    ok = codes == [200, 200, 200]
    print(f"[{'PASS' if ok else 'FAIL'}] D 上游恢复之后，熔断器自己出不来")
    print(f"        顶开熔断={tripped} · 上游已恢复 · 新用户 /api/game/start → {codes}")
    if not ok:
        print("        上游是好的，人却接不进来——这台服务要重启才能复原")
    return ok


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑某一个场景（A/B/C/D）")
    args = ap.parse_args()

    ok = True
    for spec in SCENARIOS:
        if args.only and not spec["name"].startswith(args.only.upper()):
            continue
        ok = await scenario(**spec) and ok  # type: ignore[arg-type]
        print()
    if not args.only or args.only.upper() == "D":
        ok = await scenario_latch() and ok
        print()
    print("全部通过" if ok else "有用户接不进来——bug 还在")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
