"""全局统计（旁路）。

Redis 在这里只做展示用的计数：总局数、各结局占比、各钥匙命中率（§10.2）。
它**不参与任何一局对局**——没配 REDIS_URL、没装 redis 包、连不上、写超时，
游戏都照常进行，`/api/stats` 老实返回 `available: false`。

做成旁路而不是依赖，是因为投票日当天最不能接受的故障模式，是一个纯展示
功能把对局拖垮。所以这里的每一次写都满足三条：
1. 不 await 在对局路径上（fire-and-forget，见 `_spawn`）；
2. 任何异常都吞掉，只留一条 debug 日志；
3. 连接惰性建立，导入本模块不产生任何网络行为。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, Optional, Set
from urllib.parse import urlsplit, urlunsplit

from .config import settings
from .scoring import ALL_PENALTIES, KEY_VALUES, Ending

logger = logging.getLogger(__name__)

try:  # redis 是选填依赖，没装就等于没开这个功能
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - 取决于部署环境装没装
    Redis = None  # type: ignore[assignment]

# ── 键名前缀 ───────────────────────────────────────────────────────────────
#
# **2026-08-18 加的前缀，起因是大赛的 Redis 是共享实例、而且要求所有作品都用
# db0。** 原来的键叫 `stats:games`、`stats:turns`——这是任何一个参赛作品都会
# 随手取的名字。同一个 db0 里跑着几十个作品，两个用了同名键就会**互相把对方
# 的计数器加上去**，谁也看不出来，复盘里那句「别人打成什么样」会显示别人的数。
#
# 共享 db 上不许用通用键名，这一条与本作品无关，是任何人上共享 Redis 都该做的。
_NS = "ai-antifraud-persuasion"

KEY_GAMES = f"{_NS}:stats:games"
KEY_TURNS = f"{_NS}:stats:turns"
KEY_ENDINGS = f"{_NS}:stats:endings"
KEY_HITS = f"{_NS}:stats:hits"

# Redis 卡住时不能把对局拖住，超时给得比正常往返大两个数量级也才半秒
_TIMEOUT = 0.5

# create_task 的返回值不留引用会被 GC 提前回收，任务就悄悄没了
_pending: Set["asyncio.Task[None]"] = set()


def force_db0(url: str) -> str:
    """把连接串的库号钉死在 0。

    大赛的共享 Redis **要求所有作品都用 db0**（原文三个感叹号）。
    `redis://host:port` 不写库号时 redis-py 默认就是 0，但"默认是 0"和
    "写死是 0"是两回事：谁手滑写成 `/1`，写进去的数据在看板上就永远查不到，
    而且不报错——这种错只会在"为什么统计是空的"上耗掉半天。

    所以这里不信任输入：URL 带了别的库号就改回 0，并留一行日志说明改过。
    不抛错——统计是旁路功能，任何情况下都不该把服务拦住（见模块顶部）。
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - 连拆都拆不开的串，交给 redis-py 报错
        return url

    # rediss:// 与 unix:// 的 path 语义不同，只处理 redis/rediss
    if parts.scheme not in ("redis", "rediss"):
        return url

    current = parts.path.lstrip("/")
    if current in ("", "0"):
        return urlunsplit(parts._replace(path="/0"))

    logger.warning(
        "REDIS_URL 指定了 db=%s，已按大赛要求改用 db=0", current
    )
    return urlunsplit(parts._replace(path="/0"))


class Stats:
    """计数器。所有方法在 Redis 不可用时都是安全的空操作。"""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Optional[Any] = None

    @property
    def enabled(self) -> bool:
        return bool(self._url) and Redis is not None

    def _conn(self) -> Any:
        if self._client is None:
            self._client = Redis.from_url(
                force_db0(self._url),
                socket_timeout=_TIMEOUT,
                socket_connect_timeout=_TIMEOUT,
                decode_responses=True,
            )
        return self._client

    # ── 写入（对局路径上调用，一律不等结果）──────────────────

    def record_start(self) -> None:
        if self.enabled:
            _spawn(self._incr(KEY_GAMES))

    def record_turn(self, hits: Iterable[str]) -> None:
        """一轮判分。turns 是命中率的分母，所以没命中也要记。"""
        if self.enabled:
            _spawn(self._turn(list(hits)))

    def record_ending(self, kind: str) -> None:
        if self.enabled:
            _spawn(self._hincr(KEY_ENDINGS, kind))

    async def _incr(self, key: str) -> None:
        try:
            await self._conn().incr(key)
        except Exception as exc:  # noqa: BLE001 - 旁路，绝不外抛
            logger.debug("统计写入失败（已忽略）: %s", exc)

    async def _hincr(self, key: str, field: str) -> None:
        try:
            await self._conn().hincrby(key, field, 1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("统计写入失败（已忽略）: %s", exc)

    async def _turn(self, hits: list) -> None:
        try:
            pipe = self._conn().pipeline()
            pipe.incr(KEY_TURNS)
            for name in hits:
                pipe.hincrby(KEY_HITS, name, 1)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.debug("统计写入失败（已忽略）: %s", exc)

    # ── 读取（只有 /api/stats 调用）────────────────────────

    async def snapshot(self) -> Dict[str, Any]:
        """读不到就返回 available=false，不编造零值——
        「还没人玩过」和「统计挂了」是两件事，看板上不能混为一谈。"""
        if not self.enabled:
            return {"available": False}
        try:
            conn = self._conn()
            pipe = conn.pipeline()
            pipe.get(KEY_GAMES)
            pipe.get(KEY_TURNS)
            pipe.hgetall(KEY_ENDINGS)
            pipe.hgetall(KEY_HITS)
            games, turns, endings, hits = await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("统计读取失败: %s", exc)
            return {"available": False}

        games = int(games or 0)
        turns = int(turns or 0)
        endings = {k: int(v) for k, v in (endings or {}).items()}
        hits = {k: int(v) for k, v in (hits or {}).items()}
        finished = sum(endings.values())

        return {
            "available": True,
            "games": games,
            "turns": turns,
            "endings": {
                kind: {
                    "count": endings.get(kind, 0),
                    "share": _ratio(endings.get(kind, 0), finished),
                }
                # 枚举顺序即阶梯顺序，看板照着排就是对的；
                # 写死一份清单的话，加一档结局就会在这里悄悄漏掉
                for kind in (e.value for e in Ending)
            },
            # 分母是轮数而非局数：一局里同一把钥匙可以用很多次，
            # 除以局数会得出大于 1 的"命中率"
            "keys": {
                name: {"hits": hits.get(name, 0), "rate": _ratio(hits.get(name, 0), turns)}
                for name in (*KEY_VALUES, *ALL_PENALTIES)
            },
        }


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


stats = Stats(settings.redis_url)
