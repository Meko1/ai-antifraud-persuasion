"""旁路统计。

这一层要证明的只有一件事：统计坏掉的时候，游戏不受影响。
所以用例大半在测"失败路径什么都不做"，而不是测计数准不准。

规格见 docs/TECH-DESIGN.md §7.1、§10.2。
"""

import asyncio
from typing import Any, Dict, List

import pytest

from app import stats as stats_module
from app.stats import (
    KEY_ENDINGS,
    KEY_GAMES,
    KEY_HITS,
    key_trust,
    KEY_TURNS,
    Stats,
    force_db0,
)


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
        # 合规红线（8-17 补）。看板上少了它们，就答不出投票期最想知道的
        # 那个数：有多少人在劝阻的时候顺口荐了股
        "unlicensed_advice", "guaranteed_return",
        # 另外半套专业动作（8-17 补）。这几个的占比是这套统计最有意思的一栏：
        # 它量的是**有多少人根本想不到去听、去确认、去把决定权还回去**
        "reflect_feeling", "support_autonomy",
        "check_understanding", "informed_warning",
    }


def test_一局都没有时占比是零而不是崩() -> None:
    snap = asyncio.run(_wired(FakeRedis()).snapshot())

    assert snap["available"] is True
    assert snap["games"] == 0
    assert snap["endings"]["persuaded"]["share"] == 0.0
    assert snap["keys"]["scold"]["rate"] == 0.0


# ── 共享 Redis 上的两条硬约束（2026-08-18）────────────────────────────────
#
# 大赛的 Redis 是**共享实例**，而且要求所有作品都用 db0（原文三个感叹号）。
# 这两条测试守的不是本作品的功能，是"别把别人的数据搅了、别让自己的数据丢了"。


@pytest.mark.parametrize(
    ("配的", "实际连的"),
    [
        # 不写库号：redis-py 默认就是 0，但显式写出来才看得见
        ("redis://10.126.192.12:7001", "redis://10.126.192.12:7001/0"),
        # 写对了：原样
        ("redis://:pw@10.126.192.12:7001/0", "redis://:pw@10.126.192.12:7001/0"),
        # **写错了：改回 0**。手滑写成 /1，数据就进了看板查不到的地方，
        # 而且不报错——这种错只会在"为什么统计是空的"上耗掉半天
        ("redis://:pw@10.126.192.12:7001/1", "redis://:pw@10.126.192.12:7001/0"),
        ("rediss://:pw@host:7001/15", "rediss://:pw@host:7001/0"),
        # 没配就是没配，不要凭空造一个连接串出来
        ("", ""),
    ],
)
def test_库号一律钉死在零(配的: str, 实际连的: str) -> None:
    assert force_db0(配的) == 实际连的


def test_键名带作品前缀() -> None:
    """共享 db0 上不许用通用键名。

    原来叫 `stats:games`——这是任何一个参赛作品都会随手取的名字。
    几十个作品挤在同一个 db0 里，撞名就会**互相把对方的计数器加上去**，
    谁也看不出来，而复盘里那句「别人打成什么样」会显示别人的数。
    """
    for key in (KEY_GAMES, KEY_TURNS, KEY_ENDINGS, KEY_HITS, key_trust('chen')):
        assert key.startswith("ai-antifraud-persuasion:"), key


# ── 信任度分布（复盘「超过百分之多少的人」用的数据源）──────────────────


def test_被拉黑不进信任度分布() -> None:
    """被拉黑必然信任度=0——真记进去会把所有人的百分位都顶得虚高。
    CONTEXT.md「结局」原话："被拉黑是提前终止，不入档"，这里是同一条原则。"""

    async def scenario() -> Dict[str, Any]:
        fake = FakeRedis()
        s = _wired(fake)
        s.record_trust("blacklisted", 0, "chen")
        await _drain()
        return fake.store

    assert key_trust('chen') not in asyncio.run(scenario())


def test_四档结局的信任度落进对应的桶() -> None:
    async def scenario() -> Dict[str, Any]:
        fake = FakeRedis()
        s = _wired(fake)
        s.record_trust("persuaded", 83, "chen")   # 83 // 5 = 16
        s.record_trust("transferred", 2, "chen")  # 2 // 5 = 0
        s.record_trust("stalled", 100, "chen")    # 夹到最后一个桶，不是越界
        await _drain()
        return fake.store[key_trust('chen')]

    result = asyncio.run(scenario())
    assert result == {"16": 1, "0": 1, "19": 1}


def test_快照的信任度分布是长度固定的数组() -> None:
    async def scenario() -> Dict[str, Any]:
        fake = FakeRedis()
        s = _wired(fake)
        s.record_trust("persuaded", 83, "chen")
        s.record_trust("persuaded", 81, "chen")
        await _drain()
        return await s.snapshot("chen")

    snap = asyncio.run(scenario())
    assert len(snap["trust_buckets"]) == 20
    assert snap["trust_buckets"][16] == 2
    assert sum(snap["trust_buckets"]) == 2


def test_没有信任度记录时分布是全零数组而不是缺字段() -> None:
    snap = asyncio.run(_wired(FakeRedis()).snapshot())
    assert snap["trust_buckets"] == [0] * 20


def test_线索覆盖分布写得进也读得出() -> None:
    """**写进去了读不出来 = 没这个指标。**

    `record_clues` 先落的地，`snapshot` 那一侧当时漏了，于是它只在 Redis 里
    堆着——机制自证指标要能被人读到才算数。这条测试守的就是那个口子。

    下标即"这一局他抖出来了几条"，所以长度是可挖条数 + 1：**0 条也是一档，
    而且是最该看见的那一档**（打满十二轮一条线索都没露出来 = 这一局空转）。
    """
    async def scenario() -> Dict[str, Any]:
        fake = FakeRedis()
        s = _wired(fake)
        s.record_clues(3, 4, "chen")
        s.record_clues(3, 4, "chen")
        s.record_clues(0, 4, "chen")
        await _drain()
        return await s.snapshot("chen")

    snap = asyncio.run(scenario())
    assert len(snap["clue_buckets"]) == 5, "四条可挖 → 五档（0~4）"
    assert snap["clue_buckets"][3] == 2
    assert snap["clue_buckets"][0] == 1
    assert sum(snap["clue_buckets"]) == 3


def test_没有线索记录时分布是全零数组而不是缺字段() -> None:
    snap = asyncio.run(_wired(FakeRedis()).snapshot())
    assert snap["clue_buckets"] == [0] * 5


def test_线索覆盖按场景分开_且分母不许在代码里抄一个4() -> None:
    """理由同信任度：各场景各演各的，混着算会把"某个场景哑了"平摊掉。

    分母从 `app.scenario` 派生。五个场景现在恰好都是 4 条，
    抄一个常量今天完全正确、加第六个场景时静默出错。
    """
    from app.scenario import SCENARIOS

    async def scenario() -> Dict[str, Any]:
        fake = FakeRedis()
        s = _wired(fake)
        s.record_clues(4, 4, "chen")
        s.record_clues(1, 4, "zhou")
        await _drain()
        return {"chen": await s.snapshot("chen"), "zhou": await s.snapshot("zhou"),
                "liu": await s.snapshot("liu")}

    snaps = asyncio.run(scenario())
    assert snaps["chen"]["clue_buckets"][4] == 1
    assert sum(snaps["chen"]["clue_buckets"]) == 1
    assert snaps["zhou"]["clue_buckets"][1] == 1
    assert sum(snaps["liu"]["clue_buckets"]) == 0

    for scene in SCENARIOS:
        assert len(asyncio.run(_wired(FakeRedis()).snapshot(scene.id))["clue_buckets"]) \
            == len(scene.diggable) + 1, f"{scene.id} 的分布长度没跟着场景走"


def test_可挖条数为零的分母不写入() -> None:
    """`of` 是 0 的时候一个字都不该落——那是个坏调用，不是一局零覆盖。

    两者混进同一个桶，"0 条"那一档就再也说不清是他没说还是这局没线索。
    """
    async def scenario() -> Dict[str, Any]:
        fake = FakeRedis()
        s = _wired(fake)
        s.record_clues(0, 0, "chen")
        await _drain()
        return await s.snapshot("chen")

    assert sum(asyncio.run(scenario())["clue_buckets"]) == 0


def test_信任度分布按场景分开互不串味() -> None:
    """复盘那句口径写的是「只比较相同客户、相同规则版本的有效记录」。

    四个场景难度并不一样（balance_sim 实测 expert 胜率 45.8%~54.0%），
    混在一个桶里算百分位，量出来的是"你抽到的场景是难是易"，不是你打得好不好。
    这条守的就是那句文案与数据口径必须对得上。
    """

    async def scenario() -> Dict[str, Any]:
        fake = FakeRedis()
        s = _wired(fake)
        s.record_trust("persuaded", 83, "chen")   # 桶 16
        s.record_trust("persuaded", 12, "zhou")   # 桶 2
        s.record_trust("persuaded", 13, "zhou")   # 桶 2
        await _drain()
        return {
            "chen": await s.snapshot("chen"),
            "zhou": await s.snapshot("zhou"),
            "liu": await s.snapshot("liu"),
        }

    snaps = asyncio.run(scenario())
    assert sum(snaps["chen"]["trust_buckets"]) == 1
    assert snaps["chen"]["trust_buckets"][16] == 1
    assert sum(snaps["zhou"]["trust_buckets"]) == 2
    assert snaps["zhou"]["trust_buckets"][2] == 2
    # 没人玩过的场景是干净的全零，不该借到别人的样本
    assert sum(snaps["liu"]["trust_buckets"]) == 0
