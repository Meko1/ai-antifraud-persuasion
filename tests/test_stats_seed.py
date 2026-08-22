"""造对局那个脚本（tools/stats_seed.py）。

本文件**不连 Redis**：它守的是"造出来的这批数据够不够用、是不是同一批"，
以及那两条防呆。真正的写入要 Redis，手工触发。

与 test_stats.py 的分工：那边守存储（键名、分桶、场景隔离），
这边守灌进去的东西本身。
"""

import pytest

from app.scenario import SCENARIOS
from app.scoring import Ending
from tools.stats_seed import (
    LEGACY_TRUST_KEY,
    PERCENTILE_FLOOR,
    _is_local,
    seed_games,
    summarize,
)


@pytest.mark.parametrize("sid", [s.id for s in SCENARIOS])
def test_默认按入档局数凑够百分位门槛(sid: str) -> None:
    """**灌 20 局不等于有 20 个样本。**

    被拉黑不入档（app/stats.py `_LADDER_KINDS`：提前出局，信任度必然是 0，
    混进分布会把所有人的百分位顶得虚高），而加权被拉黑率有 6.5%~10.6%。
    按"灌几局"数，20 局造出来的是 17~19 个样本，**正好卡在门槛下面**——
    百分位那一行一行都不会出现，而页面上看不出任何异常。

    这条是 `--dry-run` 第一次跑就量出来的，8-18 那次八成栽在同一处。
    """
    scene = next(s for s in SCENARIOS if s.id == sid)

    batch = seed_games(scene, seed=818)

    入档 = sum(g.ending is not Ending.BLACKLISTED for g in batch)
    assert 入档 >= PERCENTILE_FLOOR
    assert len(batch) > 入档, "这批一局都没被拉黑，那门槛这条就没被真正验到"


def test_同一个种子造出同一批() -> None:
    """不可复现的造数没法排查：三天后有人问"这个 47% 是哪来的"，
    答不上来就只能整批重造，而重造出来的又是另一批。
    """
    scene = SCENARIOS[0]

    甲 = seed_games(scene, seed=818, games=30)
    乙 = seed_games(scene, seed=818, games=30)
    丙 = seed_games(scene, seed=819, games=30)

    assert 甲 == 乙
    assert 甲 != 丙


def test_不同场景造出来的不是同一批() -> None:
    """种子里带场景（`seed:{seed}:{scene.id}`）。

    四个场景共用一条随机流的话，"按场景分桶"就白分了——
    四张分布图会长得一模一样，而它们本该反映各自的难度。
    """
    批 = [seed_games(s, seed=818, games=30) for s in SCENARIOS]

    assert len({tuple(b) for b in 批}) == len(SCENARIOS)


def test_每一局都留下四个计数器要的东西() -> None:
    """8-18 那次手写 Redis 命令，**只写了 trust 一个键**，
    于是本机留下 `games=116` 而 `turns`/`endings`/`hits` 根本不存在
    这样一个自相矛盾的状态。计数器只有一起动才是自洽的。
    """
    info = summarize(seed_games(SCENARIOS[0], seed=818, games=30))

    assert info["games"] == 30
    assert int(info["turns"]) > 0
    assert sum(int(v) for v in dict(info["endings"]).values()) == 30  # type: ignore[arg-type]
    assert dict(info["hits"]), "一次命中都没有的话，钥匙命中率那一栏会全是零"


def test_清库的清单里有那把没人读的旧键() -> None:
    """8-21 信任度分布改成按场景分桶之后，旧的全局键再也没人读。

    大赛的 Redis 是**共享 db0**，留一把没人读的键在别人的库里，
    正是 8-18 那轮加命名空间前缀时要避免的那类事。
    """
    assert LEGACY_TRUST_KEY == "ai-antifraud-persuasion:stats:trust"
    assert not LEGACY_TRUST_KEY.endswith(":")
    # 新键一律带场景后缀，与旧键不会撞
    from app.stats import key_trust

    assert all(key_trust(s.id) != LEGACY_TRUST_KEY for s in SCENARIOS)
    assert key_trust("") != LEGACY_TRUST_KEY


@pytest.mark.parametrize(
    "url, 是本机",
    [
        ("redis://127.0.0.1:6379/0", True),
        ("redis://localhost:6379/0", True),
        ("", True),
        ("redis://:pw@10.126.192.12:7001/0", False),
        ("rediss://cache.example.com:6379/0", False),
    ],
)
def test_认得出连的是不是本机(url: str, 是本机: bool) -> None:
    """大赛那台是共享实例，而这批数据是造出来的。

    往共享库里灌假数据不该是一次手滑就能做到的事，所以非本机要 `--yes`。
    """
    assert _is_local(url) is 是本机
