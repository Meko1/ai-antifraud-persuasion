"""旁路统计。

这一层要证明的只有一件事：统计坏掉的时候，游戏不受影响。
所以用例大半在测"失败路径什么都不做"，而不是测计数准不准。

规格见 docs/TECH-DESIGN.md §7.1、§10.2。
"""

import asyncio
from typing import Any, Dict, List

import pytest

from app import stats as stats_module
from app.stats import KEY_ENDINGS, KEY_GAMES, KEY_HITS, KEY_TURNS, Stats


class FakePipeline:
    """够用就好：只实现 stats.py 真正用到的四个命令。"""

    def __init__(self, store: Dict[str, Any]) -> None:
        self._store = store
        self._ops: List[Any] = []

    def incr(self, key: str) -> "FakePipeline":
        self._ops.append(lambda: self._store.__setitem__(key, int(self._store.get(key, 0)) + 1))
        return self

    def hincrby(self, key: str, field: str, amount: int) -> "FakePipeline":
        def run() -> None:
            bucket = self._store.setdefault(key, {})
            bucket[field] = bucket.get(field, 0) + amount

        self._ops.append(run)
        return self

    def get(self, key: str) -> "FakePipeline":
        self._ops.append(lambda: self._store.get(key))
        return self

    def hgetall(self, key: str) -> "FakePipeline":
        self._ops.append(lambda: dict(self._store.get(key, {})))
        return self

    async def execute(self) -> List[Any]:
        return [op() for op in self._ops]


class FakeRedis:
    def __init__(self) -> None:
        self.store: Dict[str, Any] = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self.store)

    async def incr(self, key: str) -> None:
        self.store[key] = int(self.store.get(key, 0)) + 1

    async def hincrby(self, key: str, field: str, amount: int) -> None:
        bucket = self.store.setdefault(key, {})
        bucket[field] = bucket.get(field, 0) + amount


class ExplodingRedis:
    """连不上的 Redis：每个命令都炸。"""

    def pipeline(self) -> "ExplodingRedis":
        return self

    def incr(self, *a: object, **k: object) -> "ExplodingRedis":
        return self

    def hincrby(self, *a: object, **k: object) -> "ExplodingRedis":
        return self

    def get(self, *a: object, **k: object) -> "ExplodingRedis":
        return self

    def hgetall(self, *a: object, **k: object) -> "ExplodingRedis":
        return self

    async def execute(self) -> None:
        raise ConnectionError("Redis 连不上")


def _wired(client: object) -> Stats:
    s = Stats("redis://fake/0")
    s._client = client
    return s


async def _drain() -> None:
    """等 fire-and-forget 的写入落地。生产路径上没人这么干——那正是重点。"""
    while stats_module._pending:
        await asyncio.gather(*list(stats_module._pending), return_exceptions=True)


def test_没配_redis_url_时统计整体关闭() -> None:
    """选填就是选填：没配不是错误状态，也不该编出一堆零。"""
    s = Stats("")

    assert s.enabled is False
    # 关掉时写入必须是纯空操作，连任务都不该起
    s.record_start()
    s.record_turn(["socratic_question"])
    s.record_ending("persuaded")
    assert not stats_module._pending


def test_没配_redis_时快照报告不可用() -> None:
    assert asyncio.run(Stats("").snapshot()) == {"available": False}


def test_redis_炸了也不往对局路径上抛() -> None:
    """整个旁路设计成立与否，全押在这一条上。"""

    async def scenario() -> Dict[str, Any]:
        s = _wired(ExplodingRedis())
        s.record_start()
        s.record_turn(["scold"])
        s.record_ending("transferred")
        await _drain()          # 任务里抛的异常不能逃出来
        return await s.snapshot()

    # 读侧同样吞掉异常，退回"不可用"而不是 500
    assert asyncio.run(scenario()) == {"available": False}


def test_记录开局与结局() -> None:
    async def scenario() -> Dict[str, Any]:
        fake = FakeRedis()
        s = _wired(fake)
        s.record_start()
        s.record_start()
        s.record_ending("persuaded")
        await _drain()
        return {"games": fake.store[KEY_GAMES], "endings": fake.store[KEY_ENDINGS]}

    result = asyncio.run(scenario())
    assert result["games"] == 2
    assert result["endings"] == {"persuaded": 1}


def test_没命中的轮次也计入分母() -> None:
    """命中率的分母是轮数。空手那一轮不记，命中率会被系统性高估。"""

    async def scenario() -> Dict[str, Any]:
        fake = FakeRedis()
        s = _wired(fake)
        s.record_turn(["socratic_question"])
        s.record_turn([])
        s.record_turn([])
        s.record_turn([])
        await _drain()
        return await s.snapshot()

    snap = asyncio.run(scenario())
    assert snap["turns"] == 4
    assert snap["keys"]["socratic_question"] == {"hits": 1, "rate": 0.25}


def test_快照给出结局占比与全部闭集标签() -> None:
    """闭集里的标签一个都不能少：看板上"没人用过"和"这个标签不存在"
    长得一样，就没法判断是玩家不会用还是分类器不出这个标签。"""

    async def scenario() -> Dict[str, Any]:
        fake = FakeRedis()
        s = _wired(fake)
        s.record_ending("persuaded")
        s.record_ending("transferred")
        s.record_ending("transferred")
        s.record_ending("transferred")
        await _drain()
        return await s.snapshot()

    snap = asyncio.run(scenario())
    assert snap["available"] is True
    assert snap["endings"]["persuaded"] == {"count": 1, "share": 0.25}
    assert snap["endings"]["transferred"] == {"count": 3, "share": 0.75}
    assert snap["endings"]["blacklisted"] == {"count": 0, "share": 0.0}
    assert set(snap["keys"]) == {
        "anchor_real_purpose", "socratic_question", "expose_contradiction",
        "scold", "preach", "bare_assertion",
    }


def test_一局都没有时占比是零而不是崩() -> None:
    snap = asyncio.run(_wired(FakeRedis()).snapshot())

    assert snap["available"] is True
    assert snap["games"] == 0
    assert snap["endings"]["persuaded"]["share"] == 0.0
    assert snap["keys"]["scold"]["rate"] == 0.0
