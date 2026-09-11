"""接入侧的三道闸：重放、幂等、限流。

三件事共用一个原语——**原子地把一个键从"没见过"变成"见过"**，并且只有
第一次调用拿到 True。放在一个模块里是因为它们的失败模式必须一致：
Redis 不可用时三道闸一起退回单进程，而不是一道退、两道不退。

## 为什么原来那个不够

原先重放防护是 `main.py` 里一个 4096 条的 `OrderedDict`：

- 多 worker 之间不共享 —— 平台跑两个 worker，同一张令牌在两个进程各过一次；
- 服务重启后全丢 —— 重启就是一次全局赦免；
- 检查与标记不是原子的 —— 并发提交同一张令牌，两个协程可以同时通过。

而它挡的东西不是小事：服务端不存会话（ADR-0003），令牌本身就是全部状态，
留着上一轮的令牌重发就能把说砸的那一轮撤销重来。本作唯一在判的是**时机**，
能反悔时机就不存在了。

## 幂等键为什么不是令牌

令牌只能标记"这一轮**整个走完**了"。而统计与语料是在 `score` 事件那一刻
就写下去的，`score` 到 `done` 之间客户端断开并重试，同一个 `(gid, round)`
会被写第二遍——统计虚高、语料重复、完成率失真。

所以副作用用 `(gid, round)` 认领，令牌用签名认领，两把锁管两件事：

| 键                    | 挡的是                        |
|-----------------------|-------------------------------|
| `replay:<签名>`       | 玩家拿旧令牌撤销重来          |
| `turn:<gid>:<round>`  | 断流重试把同一轮写两遍        |

## Redis 挂了怎么办

**退回单进程，不拦服务。** 这是刻意的取舍：投票日 Redis 抖一下，
代价是那段时间里重放防护退化成原来那个水平；而 fail-closed 的代价是
所有人都打不了。前者是已知的、有日志的降级，后者是事故。

局限写在明处，不写在注释里就等于没写。
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict, deque
from typing import Any, Optional

from .config import settings

logger = logging.getLogger(__name__)

try:  # 与 stats / transcripts 共用同一条依赖判断
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - 取决于部署环境装没装
    Redis = None  # type: ignore[assignment]

_NS = "ai-antifraud-persuasion"

# 单进程兜底的容量。到顶丢最旧的——一张两小时前的令牌本来也过期了。
_LOCAL_LIMIT = 8192

# Redis 卡住时不能把对局拖住，与 stats 用同一个预算
_TIMEOUT = 0.5


class _LocalClaims:
    """单进程兜底。带过期时间的 OrderedDict。

    它就是原来 `main.py` 里那个，**局限一条没变**：多 worker 不共享、
    重启即失效。留着它是因为"Redis 没配"是这个作品完全支持的部署形态
    （README 写着 Redis 是旁路），那种部署下有它比没有强。
    """

    def __init__(self, limit: int = _LOCAL_LIMIT) -> None:
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._limit = limit

    def seen(self, key: str, now: float) -> bool:
        expires = self._seen.get(key)
        return expires is not None and expires > now

    def claim(self, key: str, ttl: int, now: float) -> bool:
        if self.seen(key, now):
            return False
        self._seen[key] = now + ttl
        self._seen.move_to_end(key)
        # 顺手清一批过期的，再按容量截断。不开定时器——这里本来就每轮都被调到
        while self._seen and next(iter(self._seen.values())) <= now:
            self._seen.popitem(last=False)
        while len(self._seen) > self._limit:
            self._seen.popitem(last=False)
        return True


class _LocalCounters:
    """单进程的固定窗口计数。与 `_LocalClaims` 分开，因为存的是计数不是过期时间——
    混在一个字典里，两种语义的值早晚会被同一段清理代码当成同一种东西。
    """

    def __init__(self, limit: int = _LOCAL_LIMIT) -> None:
        self._counts: "OrderedDict[str, int]" = OrderedDict()
        self._limit = limit

    def bump(self, key: str, window: int, now: float) -> int:
        """加一，返回**本次之后**的计数值。"""
        bucket = f"{key}:{int(now // window)}"
        n = self._counts.get(bucket, 0) + 1
        self._counts[bucket] = n
        self._counts.move_to_end(bucket)
        # 旧窗口的桶靠 LRU 淘汰。它们不会被再读到，占的只是一点内存
        while len(self._counts) > self._limit:
            self._counts.popitem(last=False)
        return n


class Guard:
    """三道闸。Redis 可用时走 Redis，否则退回单进程。"""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Optional[Any] = None
        self._local = _LocalClaims()
        self._counters = _LocalCounters()
        # 只在第一次降级时喊一嗓子。每轮刷一行 WARNING 会把日志淹掉，
        # 而运维需要知道的是"从什么时候开始降级的"，不是"降级了几万次"
        self._warned = False

    @property
    def shared(self) -> bool:
        """是不是真的在用共享存储。`/healthz` 要如实报出来。"""
        return bool(self._url) and Redis is not None

    def _conn(self) -> Any:
        if self._client is None:
            from .stats import force_db0

            self._client = Redis.from_url(
                force_db0(self._url),
                socket_timeout=_TIMEOUT,
                socket_connect_timeout=_TIMEOUT,
                decode_responses=True,
            )
        return self._client

    def reset(self) -> None:
        """清空单进程那两份状态。**给测试用。**

        `guard` 是模块级单例，而限流是按分钟固定窗口计数的——不清的话，
        一个测试文件里第 21 次 `POST /api/game/start` 会拿到 429，
        而失败的会是那个碰巧排在第 21 位的用例。那种红最难查：
        它跟被测的东西毫无关系，只跟执行顺序有关。
        """
        self._local = _LocalClaims()
        self._counters = _LocalCounters()
        self._warned = False

    def _degrade(self, exc: Exception) -> None:
        if not self._warned:
            self._warned = True
            logger.warning(
                "共享重放存储不可用，已退回单进程（多 worker 之间不再共享）: %s", exc
            )

    async def seen(self, key: str) -> bool:
        """**只读**地问一句"这个键见过没有"。

        它和 `claim` 的分工不是重复：令牌重放要的是"进门时先看一眼，
        整轮走完了再记上"——进门那一眼要是顺手记上了，网关抖一下导致这一轮
        失败时，玩家刚说的那句话就永远发不出去了（那张令牌已经被标成用过）。
        """
        if self.shared:
            try:
                return bool(await self._conn().exists(f"{_NS}:{key}"))
            except Exception as exc:  # noqa: BLE001 - 降级，不拦服务
                self._degrade(exc)
        return self._local.seen(key, time.time())

    async def claim(self, key: str, *, ttl: int) -> bool:
        """把 `key` 从"没见过"改成"见过"。只有第一次调用返回 True。

        Redis 侧用 `SET NX EX`——**一条命令完成检查与标记**，
        这正是原来那个"先 in 再赋值"缺的原子性。副作用（统计、语料）
        全部挂在它上面，因此断流重试写不进第二遍。
        """
        now = time.time()
        if self.shared:
            try:
                ok = await self._conn().set(f"{_NS}:{key}", "1", nx=True, ex=ttl)
                return bool(ok)
            except Exception as exc:  # noqa: BLE001 - 降级，不拦服务
                self._degrade(exc)
        return self._local.claim(key, ttl, now)

    async def allow(self, key: str, *, limit: int, window: int = 60) -> bool:
        """固定窗口限流。`limit <= 0` 表示不限（本机调试用）。

        固定窗口不是最精确的算法（窗口边界上可以打出两倍量），
        但它只花一条 `INCR`——而这里要挡的是"一条 curl 循环吃掉共享网关额度"，
        不是精确整形。为这个场景上滑动窗口是过度设计。
        """
        if limit <= 0:
            return True
        now = time.time()
        if self.shared:
            try:
                conn = self._conn()
                bucket = f"{_NS}:rate:{key}:{int(now // window)}"
                pipe = conn.pipeline()
                pipe.incr(bucket)
                pipe.expire(bucket, window)
                used, _ = await pipe.execute()
                return int(used) <= limit
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        return self._counters.bump(f"rate:{key}", window, now) <= limit


# ── 连续故障熔断 ──────────────────────────────────────────────────────────
#
# **它补的是降级设计里缺的那一半。**
#
# 引擎对网关故障的处理是优雅降级：演绎超时用兜底台词（L1），分类失败按中性
# 判分（L2）。那是对的——已经开打的一局不该因为网关抖一下就作废，玩家刚说的
# 那句话不该白说。
#
# 但降级只在**单局**这个尺度上是对的。网关整个挂掉的时候，它意味着：
# 每一个新点进来的用户，都会拿到一局全程兜底台词、全程中性判分的对话，
# 然后在复盘里读到一个由此推算出来的"结局"。技术故障被包装成了用户表现，
# 而他不知道。8-22 那次网关停了八个多小时。
#
# 所以再加一道：**连续失败到一定次数就不再发放新的干预**。已经在打的那一局
# 照常降级走完（不能半途掐掉），但 `/api/game/start` 开始返回 503。
#
# 要熔断的是**持续性故障**。这句话一直没变，变的是怎么判。
#
# ── 2026-09-11：原来那个判据在并发下会把自己锁死 ──────────────────────────
#
# 原判据是"连续 8 次失败"，一次成功就清零。它在单人调试时完全正确，
# 在并发下是错的，而且错法有三层，一层比一层严重：
#
# 1. **"连续 8 次"在并发下不是"连着 8 个时刻都失败"。** 40 人在线时，
#    同一瞬间的 8 个并发请求一起失败就凑满了这个数——而那只是 20% 的
#    失败率，网关好得很。这个计数器把**并发数当成了时间**。
#
# 2. **拥塞被算成了故障。** 本机并发闸排不上号（`GatewayBusy`）说明的是
#    "这一刻人多"；调用方预算走完抛出的 TimeoutError，在此之前和
#    "网关挂了"长得一模一样。人越多，这台服务越是在给自己伪造故障证据。
#
# 3. **张开之后它自己出不来。** 合上的唯一途径是 `record(ok=True)`，
#    而那一行只在**一轮对局**里被调到。张开 → 新对局一律 503 → 没有新对局
#    → 没有人能给它一次成功 → 永远张着。在场那几局打完、人走干净，
#    这台服务就再也接不进任何人，**直到重启**。
#
#    这一条才是"并发高了项目就不可用"的最后一环：故障是一阵子的，
#    锁死是永久的。
#
# 现在的判据：**一个时间窗内，失败占比高到不像拥塞，才算网关挂了**；
# 张开之后过一段冷静期自己放人进去探路；拥塞压根不记账。
class Breaker:
    """网关健康度。**进程内，不共享**——这是刻意的。

    每个 worker 各自观察自己那一份网关调用。共享的话，一个 worker 上的
    网络抖动会把所有 worker 一起熔断；而分开看，真正的整体故障本来就会
    让每个 worker 各自都数到阈值。

    `threshold` 的含义变了：它现在是**窗口内至少要攒够多少次失败**才谈得上
    熔断（量的下限），不再是"连续多少次"。光有比例不够——开服头两次调用
    都失败就是 100%，那通常只是网关在热身。
    """

    def __init__(
        self,
        threshold: int = 8,
        *,
        window: float = 60.0,
        failure_ratio: float = 0.75,
        cooldown: float = 20.0,
    ) -> None:
        self._threshold = threshold
        self._window = window
        self._ratio = failure_ratio
        self._cooldown = cooldown
        self._events: "deque[tuple[float, bool]]" = deque()
        self._opened_at: Optional[float] = None

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] > self._window:
            self._events.popleft()

    @property
    def open(self) -> bool:
        """熔断了没有。True = 停止发放新干预。

        **读这一位可能把自己从张开状态里放出来**（半开）。看着不纯，
        但这是唯一正确的地方：冷静期到了要不要再试一次，只有在"有人来敲门"
        的那一刻问才有意义，而这个属性就是那扇门。没有它，熔断器只能靠
        已经在打的那几局把自己救出来——人走干净就再也救不回来了。
        """
        now = time.monotonic()
        self._prune(now)
        if self._opened_at is None:
            return False
        if now - self._opened_at < self._cooldown:
            return True
        # 半开：冷静期满，放人进去探路。探成了会 record(ok=True)，
        # 探砸了会重新攒够失败再张开——两条路都走得通，不必重启。
        logger.warning("熔断冷静期满，放行新干预探一次路")
        self._opened_at = None
        self._events.clear()
        return False

    def record(self, *, ok: bool, busy: bool = False) -> None:
        """记一次网关调用的结果。

        `busy=True` 表示这一次压根没打到网关（本机并发闸排不上号）。
        **它一个字都不记**：拥塞不是网关的健康状况，拿它熔断就是
        "人一多就关门"。
        """
        if busy:
            return
        now = time.monotonic()
        self._events.append((now, ok))
        self._prune(now)

        if ok:
            if self._opened_at is not None:
                self._opened_at = None
                self._events.clear()
                logger.warning("网关恢复，重新发放新干预")
            return
        if self._opened_at is not None:
            return

        failures = sum(1 for _, healthy in self._events if not healthy)
        if failures >= self._threshold and failures >= self._ratio * len(self._events):
            self._opened_at = now
            logger.error(
                "近 %.0fs 内 %s/%s 次网关调用失败，暂停发放新干预 %.0fs"
                "（已开局的对局照常降级走完）",
                self._window, failures, len(self._events), self._cooldown,
            )

    def snapshot(self) -> dict:
        """给 `/healthz` 用。**兜底台词是故意让人察觉不出来的**，熔断状态
        因此必须自己报出来，否则运维只能从"没人能进来"倒推。"""
        now = time.monotonic()
        self._prune(now)
        failures = sum(1 for _, healthy in self._events if not healthy)
        return {
            "open": self.open,
            "failures": failures,
            "samples": len(self._events),
            "window_seconds": self._window,
        }

    def reset(self) -> None:
        self._events.clear()
        self._opened_at = None


breaker = Breaker()


# ── 键名 ──────────────────────────────────────────────────────────────────
#
# 拼在一处，省得两个调用点各拼一份然后走散。

def replay_key(token: str) -> str:
    """令牌的签名部分。它已经是 payload 的 HMAC，够做唯一标识，也不必存正文。"""
    return f"replay:{token.rpartition('.')[2]}"


def turn_key(gid: str, round_: int) -> str:
    """一轮的业务幂等键。**统计与语料都认这一个**，两边不会各写各的。"""
    return f"turn:{gid}:{round_}"


# 单例。与 `stats` / `transcripts` 同一个做法：连接惰性建立，
# 导入本模块不产生任何网络行为。
#
# **放在这里而不是 main.py**：测试要复位它（见 tests/conftest.py 那条
# autouse 夹具），而"从定义它的模块里 import 它"是唯一不会让人找错地方的写法。
guard = Guard(settings.redis_url)
