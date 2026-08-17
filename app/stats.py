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

from .config import settings
from .scoring import KEY_VALUES, PENALTY_VALUES, Ending

logger = logging.getLogger(__name__)

try:  # redis 是选填依赖，没装就等于没开这个功能
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - 取决于部署环境装没装
    Redis = None  # type: ignore[assignment]

KEY_GAMES = "stats:games"
KEY_TURNS = "stats:turns"
KEY_ENDINGS = "stats:endings"
KEY_HITS = "stats:hits"

# Redis 卡住时不能把对局拖住，超时给得比正常往返大两个数量级也才半秒
_TIMEOUT = 0.5

# create_task 的返回值不留引用会被 GC 提前回收，任务就悄悄没了
_pending: Set["asyncio.Task[None]"] = set()


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
                self._url,
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
                for name in (*KEY_VALUES, *PENALTY_VALUES)
            },
        }


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


stats = Stats(settings.redis_url)
