"""判分引擎。

领域语言见 CONTEXT.md，求值规格见 docs/TECH-DESIGN.md §3。
判分是纯函数：不碰模型、不碰 IO，因此这里的每条断言都是对规格的直接陈述。
"""

import pytest

from app.scoring import Ending, GameState, Mood, evaluate_turn, mood_for, new_game


def test_未命中任何钥匙时只吃信任流失() -> None:
    outcome = evaluate_turn(new_game(), hit_keys=[], grounded=False)

    # 开局 32，无条件流失 −2
    assert outcome.state.trust == 30
    # delta 只记判分结果；这一轮的下降完全来自信任流失，判分为 0
    assert outcome.delta == 0


def test_首次命中钥匙且扎根时拿满基值() -> None:
    # 用第 5 轮的状态，避开前 3 轮封顶，让这条测试只讲"满权重"这一件事
    state = GameState(round=4, trust=40, pool=0, used={})

    outcome = evaluate_turn(state, hit_keys=["anchor_real_purpose"], grounded=True)

    assert outcome.delta == 25
    assert outcome.state.trust == 63  # 40 + 25 − 2
    # 钝化要靠它计数，所以命中必须被记下来
    assert outcome.state.used["anchor_real_purpose"] == 1


@pytest.mark.parametrize(
    ("已用次数", "预期得分"),
    [(0, 20), (1, 10), (2, 5), (3, 0), (7, 0)],
)
def test_钥匙钝化随使用次数递减(已用次数: int, 预期得分: int) -> None:
    """第 1/2/3/4+ 次命中权重 1.0 / 0.5 / 0.25 / 0。

    复读一招会迅速失效，玩家必须把三把钥匙组合用完。
    """
    state = GameState(
        round=5, trust=40, pool=0, used={"socratic_question": 已用次数}
    )

    outcome = evaluate_turn(state, hit_keys=["socratic_question"], grounded=True)

    assert outcome.delta == 预期得分


@pytest.mark.parametrize(
    ("钥匙", "预期得分"),
    [
        ("socratic_question", 9),    # 20 × 0.45 = 9
        ("anchor_real_purpose", 11),  # 25 × 0.45 = 11.25，四舍五入取 11
    ],
)
def test_未扎根的命中效力大打折扣(钥匙: str, 预期得分: int) -> None:
    """扎根门控堵的是照攻略复读固定句子。

    §9.2 的对照实验：关掉它，parrot 人设胜率从 11.7% 跳到 100%。
    """
    outcome = evaluate_turn(new_game(), hit_keys=[钥匙], grounded=False)

    assert outcome.delta == 预期得分


def test_失误扣分不受钝化与扎根影响() -> None:
    """责骂第四次仍然照扣 −5：钝化是给钥匙的优待，不是给失误的赦免。"""
    state = GameState(round=5, trust=40, pool=0, used={"scold": 3})

    outcome = evaluate_turn(state, hit_keys=["scold"], grounded=False)

    assert outcome.delta == -5
    assert outcome.state.trust == 33  # 40 − 5 − 2
    # 失误不进钝化计数表
    assert outcome.state.used["scold"] == 3


def test_单轮判分被钳制在上限() -> None:
    """一轮内三把钥匙全中也不能一击制胜，否则 12 轮的节奏就没了。"""
    state = GameState(round=5, trust=40, pool=0, used={})

    outcome = evaluate_turn(
        state,
        hit_keys=["anchor_real_purpose", "socratic_question", "expose_contradiction"],
        grounded=True,
    )

    assert outcome.delta == 25  # 25 + 20 + 20 = 65，钳到 25
    assert outcome.state.trust == 63  # 40 + 25 − 2


@pytest.mark.parametrize(
    ("初始信任", "预期信任"),
    [(9, 8), (8, 8), (5, 5)],
)
def test_信任流失不会把人压到地板以下(初始信任: int, 预期信任: int) -> None:
    """光靠时间流逝不该把玩家踢出局——novice 也要能平均玩到 11 轮。"""
    state = GameState(round=5, trust=初始信任, pool=0, used={})

    outcome = evaluate_turn(state, hit_keys=[], grounded=False)

    assert outcome.state.trust == 预期信任


def test_主动失误仍能击穿地板() -> None:
    """地板保护的是流失，不是失误。被拉黑必须是玩家自己作出来的。"""
    state = GameState(round=5, trust=9, pool=0, used={})

    outcome = evaluate_turn(state, hit_keys=["scold"], grounded=False)

    assert outcome.state.trust == 3  # 9 − 5（责骂）− 1（流失被地板削到 −1）


def test_前三轮封顶但超出部分存入蓄势池() -> None:
    """开局就打满的玩家不该被白白浪费掉分数。

    若前 3 轮是硬上限，真懂反诈的人反而吃亏、胡聊的人赢得更快——
    规则会把"懂行"变成惩罚。超出部分入池，稍后逐轮释放。
    """
    outcome = evaluate_turn(
        new_game(),
        hit_keys=["anchor_real_purpose", "socratic_question", "expose_contradiction"],
        grounded=True,
    )

    assert outcome.state.trust == 50  # 32 + 25 − 2 = 55，封顶到 50
    assert outcome.state.pool == 5  # 溢出的 5 分没有蒸发


@pytest.mark.parametrize(
    ("池中存量", "预期信任", "预期余量"),
    [
        (20, 63, 5),  # 每轮最多放 15
        (8, 56, 0),   # 不足一次释放量就一次放完
    ],
)
def test_第四轮起蓄势池逐轮释放(池中存量: int, 预期信任: int, 预期余量: int) -> None:
    state = GameState(round=3, trust=50, pool=池中存量, used={})

    outcome = evaluate_turn(state, hit_keys=[], grounded=False)

    assert outcome.state.trust == 预期信任
    assert outcome.state.pool == 预期余量


@pytest.mark.parametrize(
    ("初始信任", "命中", "预期信任"),
    [
        (90, ["anchor_real_purpose", "socratic_question", "expose_contradiction"], 100),
        (2, ["scold"], 0),
    ],
)
def test_信任度始终落在合法区间(
    初始信任: int, 命中: list[str], 预期信任: int
) -> None:
    state = GameState(round=5, trust=初始信任, pool=0, used={})

    outcome = evaluate_turn(state, hit_keys=命中, grounded=True)

    assert outcome.state.trust == 预期信任


@pytest.mark.parametrize(
    ("轮次", "初始信任", "命中", "预期结局"),
    [
        # 信任度达标即劝住，不必等轮次耗尽
        (5, 60, ["anchor_real_purpose", "socratic_question", "expose_contradiction"],
         Ending.PERSUADED),
        # 归零即被拉黑，对局提前终止
        (5, 2, ["scold"], Ending.BLACKLISTED),
        # 第 12 轮结束仍未达标，他把钱转走了
        (11, 40, [], Ending.TRANSFERRED),
        # 对局仍在进行
        (5, 40, [], None),
    ],
)
def test_结局判定(
    轮次: int, 初始信任: int, 命中: list[str], 预期结局: Ending | None
) -> None:
    state = GameState(round=轮次, trust=初始信任, pool=0, used={})

    outcome = evaluate_turn(state, hit_keys=命中, grounded=True)

    assert outcome.ending is 预期结局


@pytest.mark.parametrize(
    ("信任度", "预期档位"),
    [
        (0, Mood.GUARDED),
        (24, Mood.GUARDED),
        (25, Mood.IRRITATED),
        (32, Mood.IRRITATED),  # 开局落在烦躁，不是最高戒备
        (44, Mood.IRRITATED),
        (45, Mood.WAVERING),
        (64, Mood.WAVERING),
        (65, Mood.SOFTENING),
        (100, Mood.SOFTENING),
    ],
)
def test_情绪档位由信任度映射(信任度: int, 预期档位: Mood) -> None:
    assert mood_for(信任度) is 预期档位
