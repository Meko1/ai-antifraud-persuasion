"""并发压测（§10.3）。

上线检查单上唯一一条没法靠单元测试守住的门槛：**50 并发对局下，首句延迟
P95 ≤ 3s**（§9.4）。它量的不是本机 CPU——等待模型的对局只是挂起的 async
task——而是网关并发额度撑不撑得住（§6.1）。

首句延迟的定义在这里很重要：从发出 `/api/game/turn` 到收到**第一个
`sentence` 事件**的耗时。不是整轮耗时。玩家感知的快慢就在这一下，
后面的句子是流着出来的，他一边读一边等，等不到才叫卡。

脚本打的是真实 HTTP 接口，因此**需要服务已经起着**，也**会真实花钱调模型**，
所以不进 pytest、不进 CI。

用法：
    python -m tools.loadtest                        # 50 并发 × 3 轮，跑 §10.3 门槛
    python -m tools.loadtest --concurrency 10       # 先小跑一次确认链路通
    python -m tools.loadtest --rounds 12            # 打满一整局
    python -m tools.loadtest --base-url http://1.2.3.4:21818   # 打部署好的机器
    python -m tools.loadtest --ramp 5               # 5 秒内陆续进场，模拟真实到达

降级率一并报出来但不设门槛：降级本身是设计好的行为（§6.2），
真正要盯的是它有没有从"偶尔"变成"常态"。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:21818"

# §9.4：首句延迟 P95 ≤ 3s
P95_CEILING_SECONDS = 3.0

# 压测要打在真实判分路径上，所以这些话得像人说的：全喂"测试测试"会让
# 分类请求走进一条最短的路，量出来的延迟偏乐观。
UTTERANCES: Tuple[str, ...] = (
    "这笔钱本来是准备做什么用的？",
    "你说群里几百号人都在跟，那你认识其中几个？",
    "王老师要是那么有把握，他自己怎么不借钱去买？",
    "先别急着转，你试着提一笔小的出来看看能不能到账。",
    "那个账户的开户名，你亲眼看过吗？是公司还是个人？",
    "你跟我说说，前面那几次赚的钱，你取出来花了吗？",
    "孩子结婚的日子定了没有？",
    "你老伴知道这三十万的事吗？",
    "你说这是内部消息，那为什么要告诉几百个人？",
    "我不是要拦你，我就想弄明白一件事。",
)


@dataclass
class Sample:
    """一轮的观测。失败的轮次也要留下来，否则失败越多延迟越好看。"""

    first_sentence: Optional[float] = None   # 秒；None 表示压根没等到台词
    total: float = 0.0
    degraded: bool = False
    error: Optional[str] = None


@dataclass
class Report:
    samples: List[Sample] = field(default_factory=list)
    start_failures: int = 0
    wall_clock: float = 0.0

    @property
    def latencies(self) -> List[float]:
        return sorted(s.first_sentence for s in self.samples if s.first_sentence is not None)

    @property
    def degraded(self) -> int:
        return sum(1 for s in self.samples if s.degraded)

    @property
    def errors(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for s in self.samples:
            if s.error:
                out[s.error] = out.get(s.error, 0) + 1
        return out


def percentile(values: Sequence[float], q: float) -> float:
    """最近秩法。样本量小的时候插值会造出一个没人经历过的延迟。"""
    if not values:
        return float("nan")
    rank = max(1, min(len(values), int(-(-q * len(values) // 1))))
    return values[rank - 1]


async def _play_turn(client: httpx.AsyncClient, token: str, utterance: str) -> Tuple[Sample, str]:
    """打一轮，返回观测与下一轮的令牌（拿不到就是空串）。"""
    sample = Sample()
    next_token = ""
    began = time.perf_counter()

    try:
        async with client.stream(
            "POST", "/api/game/turn", json={"token": token, "utterance": utterance}
        ) as resp:
            if resp.status_code != 200:
                sample.error = f"http_{resp.status_code}"
                sample.total = time.perf_counter() - began
                return sample, ""

            name = ""
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    payload = _load(line[len("data:"):].strip())
                    if name == "sentence" and sample.first_sentence is None:
                        sample.first_sentence = time.perf_counter() - began
                    elif name == "score":
                        sample.degraded = bool(payload.get("degraded"))
                    elif name == "state":
                        next_token = payload.get("token", "")
                    elif name == "error":
                        sample.error = payload.get("code", "unknown")
    except Exception as exc:  # noqa: BLE001 - 压测器自己不能把异常吞没了当成功
        sample.error = type(exc).__name__

    sample.total = time.perf_counter() - began
    return sample, next_token


def _load(raw: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


async def _play_game(
    client: httpx.AsyncClient, rounds: int, rng: random.Random, report: Report, delay: float
) -> None:
    if delay:
        await asyncio.sleep(delay)

    try:
        resp = await client.post("/api/game/start")
        resp.raise_for_status()
        token = resp.json()["token"]
    except Exception:  # noqa: BLE001
        report.start_failures += 1
        return

    for _ in range(rounds):
        sample, token = await _play_turn(client, token, rng.choice(UTTERANCES))
        report.samples.append(sample)
        # 令牌断了就没法继续这一局：后面每轮都会是 invalid_state，
        # 那不是延迟数据，是噪声
        if not token:
            break


async def run(
    *, base_url: str, concurrency: int, rounds: int, ramp: float, seed: int
) -> Report:
    report = Report()
    rng = random.Random(seed)
    limits = httpx.Limits(max_connections=concurrency + 10, max_keepalive_connections=concurrency)

    began = time.perf_counter()
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0, limits=limits) as client:
        await asyncio.gather(
            *(
                _play_game(
                    client,
                    rounds,
                    random.Random(rng.random()),
                    report,
                    delay=(ramp * i / concurrency) if ramp else 0.0,
                )
                for i in range(concurrency)
            )
        )
    report.wall_clock = time.perf_counter() - began
    return report


def format_report(report: Report, *, concurrency: int, rounds: int) -> str:
    lat = report.latencies
    total = len(report.samples)
    lines = [
        f"并发 {concurrency} 局 × {rounds} 轮，耗时 {report.wall_clock:.1f}s",
        f"轮次 {total}，拿到台词 {len(lat)}，开局失败 {report.start_failures}",
        "",
    ]

    if lat:
        lines += [
            "首句延迟（秒）",
            f"  P50 {percentile(lat, 0.50):6.2f}",
            f"  P95 {percentile(lat, 0.95):6.2f}",
            f"  P99 {percentile(lat, 0.99):6.2f}",
            f"  max {lat[-1]:6.2f}   mean {statistics.fmean(lat):.2f}",
            "",
        ]
    else:
        lines += ["首句延迟：无有效样本", ""]

    rate = report.degraded / total if total else 0.0
    lines.append(f"降级轮次 {report.degraded}/{total}（{rate:.1%}）——L1/L3 走了兜底台词，属预期行为")

    if report.errors:
        lines.append("错误分布：")
        for code, count in sorted(report.errors.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {code}: {count}")
    else:
        lines.append("无错误事件")

    return "\n".join(lines)


def check_threshold(report: Report) -> List[str]:
    """§9.4 的门槛。样本不足也算不通过——量不出来不等于达标。"""
    lat = report.latencies
    if not lat:
        return ["首句延迟：一个有效样本都没有，无法判定（服务起着吗？网关通吗？）"]

    p95 = percentile(lat, 0.95)
    if p95 > P95_CEILING_SECONDS:
        return [f"首句延迟 P95 {p95:.2f}s > {P95_CEILING_SECONDS}s"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="并发压测（§10.3）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="被测服务地址")
    parser.add_argument("--concurrency", type=int, default=50, help="并发对局数")
    parser.add_argument("--rounds", type=int, default=3, help="每局打几轮")
    parser.add_argument("--ramp", type=float, default=0.0, help="进场铺开的秒数，0 为同时进场")
    parser.add_argument("--seed", type=int, default=20260812, help="选句随机种子")
    args = parser.parse_args()

    print(
        f"压测 {args.base_url}：{args.concurrency} 并发 × {args.rounds} 轮…",
        flush=True,
    )
    report = asyncio.run(
        run(
            base_url=args.base_url,
            concurrency=args.concurrency,
            rounds=args.rounds,
            ramp=args.ramp,
            seed=args.seed,
        )
    )

    print()
    print(format_report(report, concurrency=args.concurrency, rounds=args.rounds))
    print()

    failures = check_threshold(report)
    if failures:
        print("§9.4 门槛未通过：")
        for f in failures:
            print(f"  ✗ {f}")
        return 1

    print(f"§9.4 首句延迟门槛通过（P95 ≤ {P95_CEILING_SECONDS}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
