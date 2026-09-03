"""线索覆盖率的口径（tools/cue_coverage.py）。

**这个脚本不调模型，但它读的语料是调模型跑出来的**，所以分工照 `act_eval`
那一套：跑批本身不进 pytest，**口径进**。这里守的是"数得对不对"，
不是"覆盖率高不高"——后者是产品判断，由跑批的输出交给人看。

守的三件事，每一件都对应一次已经踩过的坑：

1. **缺轮的局要剔掉。** 掉一轮就少一次抖出线索的机会，覆盖率只会被压低，
   是个方向确定的偏差。
2. **分母是 `test` 非空的那几条。** 开局就在投顾手里的那条（`own=True`）
   不算，与复盘揭晓同一个定义。
3. **"路线问过没有"认的是玩家那一侧。** 这一列是用来把"他不肯说"和
   "没人问过他"分开的，认错一侧整列就失去意义。
"""

import pytest

from app.scenario import SCENARIOS
from tools.cue_coverage import (
    asked_by,
    conversations,
    cover,
    diggable,
    first_round,
    moods_for,
    routes_for,
)

场景 = [pytest.param(s, id=s.id) for s in SCENARIOS]


def 行(route: str, run: int, round_: int, raw: str) -> dict:
    return {"route": route, "run": run, "round": round_, "raw": raw}


# ── 折局与剔缺轮 ──────────────────────────────────────────────────────────


def test_按路线与遍数折成一局一局() -> None:
    rows = [行("climb", 0, 2, "第二句"), 行("climb", 0, 1, "第一句"),
            行("cold", 0, 1, "另一局"), 行("cold", 0, 2, "另一局二")]

    convs, dropped = conversations(rows)

    assert dropped == 0
    assert convs[("climb", 0)] == ["第一句", "第二句"], "轮次乱序进来也要按轮排好"
    assert convs[("cold", 0)] == ["另一局", "另一局二"]


def test_缺轮的局被剔掉且数目要报出来() -> None:
    """掉轮不是中性的：少一轮就少一次抖出线索的机会。

    把它留在分母里，得到的是一个**方向确定**的向下偏差，
    而这个脚本量的正是覆盖率本身。
    """
    rows = [行("climb", 0, 1, "a"), 行("climb", 0, 2, "b"),
            行("climb", 1, 1, "c")]  # 第 2 轮掉了

    convs, dropped = conversations(rows)

    assert dropped == 1
    assert list(convs) == [("climb", 0)]


def test_满轮数从语料自己推_不写死十二() -> None:
    """`--rounds 6` 跑出来的语料只有六轮，写死 12 会把整批判成缺轮。"""
    rows = [行("climb", r // 6, r % 6 + 1, "x") for r in range(12)]

    convs, dropped = conversations(rows)

    assert dropped == 0
    assert len(convs) == 2


def test_全部缺轮时不崩_返回空() -> None:
    convs, dropped = conversations([])

    assert convs == {} and dropped == 0


# ── 分母与判据 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scene", 场景)
def test_分母是可挖的那几条_自带那条不算(scene) -> None:
    """与复盘揭晓同一个定义：`test` 非空 ⇔ 不带 `own`。

    这两侧一旦分岔，落进 Redis 的 `clue_buckets` 分母和屏幕上印的分母
    就不是同一个数了，而两处都不会报错。
    """
    名字 = [n for n, _ in diggable(scene)]

    assert 名字 == [r.name for r in scene.phone if not r.own]
    assert len(名字) == len(scene.diggable)


def test_命中判在整局上_不要求同一轮里说全() -> None:
    """线索是一整局挖出来的，不是一句话里说全的。"""
    scene = next(s for s in SCENARIOS if s.id == "chen")
    clues = diggable(scene)

    分散 = cover(["提到王老师", "……", "我女儿小雨也说过"], clues)
    集中 = cover(["王老师和我女儿小雨"], clues)

    assert sum(分散) == sum(集中) == 2


def test_首现轮次从一起算_没抖出来返回零() -> None:
    scene = next(s for s in SCENARIOS if s.id == "chen")
    _, rx = diggable(scene)[0]  # 启航财经/王老师那条

    assert first_round(["没说", "王老师说的", "王老师又说"], rx) == 2
    assert first_round(["没说", "还是没说"], rx) == 0


# ── "路线问过没有"这一列 ──────────────────────────────────────────────────


def test_问过没有认的是玩家那一侧且只看首现之前() -> None:
    """玩家在第 R 轮先说、他在第 R 轮才回，所以第 R 轮玩家那句算在"已问"里。"""
    scene = next(s for s in SCENARIOS if s.id == "chen")
    _, rx = diggable(scene)[0]
    玩家 = ["这笔钱做什么用", "王老师是谁", "那只票呢"]

    assert asked_by(玩家, rx, upto=2) is True, "第 2 轮问的，首现在第 2 轮就算问过"
    assert asked_by(玩家, rx, upto=1) is False, "第 1 轮还没问到，就是没问"


@pytest.mark.parametrize("scene", 场景)
def test_每个场景四条路线都取得到发言与档位(scene) -> None:
    """这两列是拿来把"他不肯说"和"没人问过"分开的。

    取不到就退化成一列空的横杠，而空横杠读起来跟"没问"一模一样——
    一个会让人读反结论的静默失败。
    """
    says = routes_for(scene.id)
    moods = moods_for(scene.id)

    assert set(says) == {"cold", "climb", "sawtooth", "parrot"}
    assert set(moods) == set(says)
    for rid in says:
        assert len(says[rid]) == len(moods[rid]), f"{scene.id}/{rid} 发言与档位对不上"
