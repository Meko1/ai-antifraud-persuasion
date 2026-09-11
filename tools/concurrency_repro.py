"""并发下「暂时无法接入客户」的复现环（2026-09-11）。

    python -m tools.concurrency_repro            # 四个场景全跑，约 20 秒
    python -m tools.concurrency_repro --only C   # 只跑用户报的那一条

**和 `tools/loadtest.py` 是两件事**：那个打真实 HTTP、真实花钱，量的是
首句延迟 P95；这个在进程内用 ASGI 直连、上游全是假的，**量的是有没有人
被挡在门外**。判据只有一条——有没有用户在 `POST /api/game/start` 上拿到
非 200。那一下就是前端（static/opening.js）打出「暂时无法接入客户。」
的时刻。

四个场景各自钉一条已经修过的路：

  A 对照组，上游健康、每人一个 IP —— 本来就该全进得来
  B 反代不透传真实 IP，所有人挤在一个限流桶里（app/config.py 的
    RATE_LIMIT_START 那段注释）
  C **用户报的那一条**：上游没坏，只是人多到本机并发闸排不过来。
    这种时候一个人都不该被挡住，该发生的只是"这一轮走兜底台词"
  D 上游真挂了：熔断张开是对的，**但上游恢复之后它得自己合上**
    （app/guard.py 的 Breaker，那一层是"项目不可用"的出处）

改动 app/gateway.py、app/guard.py、app/engine.py 之后跑一遍这个。
逐层的快速回归在 tests/test_concurrency_lockout.py，不用起服务。
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

from app import gateway as gateway_mod  # noqa: E402
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


class DeadClient:
    """网关每次都回 400。**取自 09-11 那天的真实日志**（Bedrock 那条
    ValidationException）——它不是 429，ADR-0007 的自动切换判不到它。"""

    def __init__(self) -> None:
        self.calls = 0
        self.rejected = 0
        self.cfg = SimpleNamespace(
            provider="internal", model="fake-internal", protocol="anthropic"
        )

    def _boom(self) -> LLMError:
        self.calls += 1
        self.rejected += 1
        return LLMError("调用大模型失败: BadRequestError: Error code: 400")

    async def chat(self, messages: List[Dict[str, str]], **_: Any) -> str:
        raise self._boom()

    async def stream(self, messages: List[Dict[str, str]], **_: Any) -> AsyncIterator[str]:
        raise self._boom()
        yield ""  # pragma: no cover - 让它是个异步生成器


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
    gates: tuple = (),
    late: int = 0,
) -> bool:
    guard.reset()
    breaker.reset()
    # `gates` 把本机那两道并发闸缩小，用几十个人站出"几百人打十六个名额"
    # 的队形——**要复现的是"并发远超闸门额度"这个比例**，不是那两个具体数字。
    saved = (gateway_mod.ACT_GATE, gateway_mod.CLASSIFY_GATE)
    if gates:
        gateway_mod.ACT_GATE = asyncio.Semaphore(gates[0])
        gateway_mod.CLASSIFY_GATE = asyncio.Semaphore(gates[1])
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

        results = list(await asyncio.gather(*(staggered(i) for i in range(users))))
        # **高峰过去之后才点进来的那几个人。** 他们是"随机接入客户直接报错"
        # 里最刺眼的一批：高峰已经散了、上游好着，他们照样接不进去——
        # 因为熔断器在高峰期间被拥塞顶开了，而它自己出不来。
        for i in range(late):
            results.append(await one_user(client, 9000 + i, xff=xff, rounds=0))
    elapsed = time.monotonic() - t0
    app.dependency_overrides.pop(get_gateway, None)
    gateway_mod.ACT_GATE, gateway_mod.CLASSIFY_GATE = saved

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
    # 用户报的就是这一条：**大模型并发过高导致服务异常**。
    # 上游没坏，只是人多到本机并发闸排不过来——这种时候一个人都不该被挡在门外，
    # 该发生的只是"这一轮走兜底台词"。
    dict(name="C 上游没坏，只是人多到排不过来（用户报的那一条）",
         users=120, rounds=1, ramp=12.0, xff=True,
         capacity=256, latency=1.0, rpm=0, gates=(3, 4), late=5),
)


async def scenario_latch() -> bool:
    """D 上游真挂了，熔断张开是对的；**但上游恢复之后它得自己合上。**

    原先合上的唯一途径是 `record(ok=True)`，而那一行只在**一轮对局**里被调到。
    张开 → 新对局一律 503 → 没有新对局 → 没有人能给它一次成功 → 永远张着。
    在场的那几局打完、人走干净，这台服务就再也接不进任何人，直到重启。
    """
    guard.reset()
    breaker.reset()
    # 冷静期在这个复现环里缩到 1 秒，省得干等 20 秒。**判的是"会不会自己合上"，
    # 不是"几秒合上"**——时长那一格由 tests/test_gateway.py 钉。
    breaker._cooldown = 1.0

    # 全程 400：这是 09-11 那天日志里真实的那一种，**不是** 429，
    # 所以 ADR-0007 的自动切换救不了它，只剩熔断器这一道。
    dead = DeadClient()
    healthy = CongestedClient(capacity=16, latency=0.0, rpm=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://dbg", timeout=120
    ) as client:
        # 1. 上游挂着，几个人在打——把熔断器顶开
        app.dependency_overrides[get_gateway] = lambda: ModelGateway(client=dead)
        await asyncio.gather(
            *(one_user(client, i, xff=True, rounds=2) for i in range(8))
        )
        tripped = breaker.open

        # 2. 上游完全恢复，现场一个人都不剩（没有任何一局能给它一次成功）
        app.dependency_overrides[get_gateway] = lambda: ModelGateway(client=healthy)
        during = (await client.post(
            "/api/game/start", json={}, headers={"X-Forwarded-For": "10.9.0.1"},
        )).status_code
        await asyncio.sleep(1.2)

        # 3. 冷静期满，新人来了。上游是好的，他该进得去
        codes = [
            (await client.post(
                "/api/game/start", json={},
                headers={"X-Forwarded-For": f"10.9.1.{i}"},
            )).status_code
            for i in range(3)
        ]

    app.dependency_overrides.pop(get_gateway, None)
    breaker.reset()
    ok = tripped and during == 503 and codes == [200, 200, 200]
    print(f"[{'PASS' if ok else 'FAIL'}] D 上游真挂了：该张开，也该自己合上")
    print(f"        顶开熔断={tripped} · 冷静期内新用户→{during} · "
          f"冷静期满新用户→{codes}")
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
