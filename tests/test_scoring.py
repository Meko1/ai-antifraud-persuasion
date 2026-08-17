"""判分引擎。

领域语言见 CONTEXT.md，求值规格见 docs/TECH-DESIGN.md §3。
判分是纯函数：不碰模型、不碰 IO，因此这里的每条断言都是对规格的直接陈述。

这一版的核心命题是**时机**：同一把钥匙在不同情绪档位值不同的分。
下面的测试按"钥匙值多少 → 什么在削它 → 什么在放大它 → 时间在做什么"排列。
"""

import pytest

from app.scoring import (
    DRIFT,
    EFFICACY,
    MAX_ROUNDS,
    PRESSURE_DRIFT,
    WINDOW_MISSED_TRUST,
    Ending,
    GameState,
    Mood,
    decide_ending,
    evaluate_turn,
    mood_for,
    new_game,
    resistance,
    under_pressure,
)


def 对局中(**overrides: object) -> GameState:
    """第 5 轮开局时的状态。避开前 3 轮封顶，让每条测试只讲一件事。"""
    base = dict(round=4, trust=40, pool=0, used={})
    base.update(overrides)
    return GameState(**base)  # type: ignore[arg-type]


# ── 时机：本次重设计的支点 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("钥匙", "信任度", "档位", "预期得分"),
    [
        # 锚定真实用途是开场：他敌意最重的时候最有力，等他松动了再问就多余了
        ("anchor_real_purpose", 20, Mood.GUARDED, 23),
        ("anchor_real_purpose", 45, Mood.WAVERING, 14),
        # 指出内部矛盾正相反：太早用他会替骗局辩护，晃起来了才拆得动
        ("expose_contradiction", 20, Mood.GUARDED, 5),
        ("expose_contradiction", 45, Mood.WAVERING, 21),
    ],
)
def test_同一把钥匙在不同档位值不同的分(
    钥匙: str, 信任度: int, 档位: Mood, 预期得分: int
) -> None:
    """三把钥匙等值时，最优解就是循环使用，玩法退化成过清单。

    效力矩阵把「背下三把钥匙」变成「判断现在该用哪一把」——玩家必须先读人。
    这几条用钝化过一次的状态取值，是为了避开单轮钳制，让矩阵本身可见。
    """
    state = 对局中(trust=信任度, used={钥匙: 1})
    assert mood_for(信任度) is 档位

    outcome = evaluate_turn(state, hit_keys=[钥匙], grounded=True)

    assert outcome.delta == 预期得分


def test_在他还戒备时硬拆矛盾会顶起防御姿态() -> None:
    """这是本作最想让玩家学到的一条真东西。

    太早指出矛盾，他不会认，只会替骗局辩护，反而更 entrenched——
    所以除了当轮打三折，还要留下一轮听不进话的后遗症。
    """
    outcome = evaluate_turn(
        对局中(trust=20, used={"expose_contradiction": 1}),
        hit_keys=["expose_contradiction"],
        grounded=True,
    )

    assert outcome.state.guard == 1


# ── 钥匙的基本效力 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("已用次数", "预期得分"),
    [(0, 18), (1, 13), (2, 8), (3, 5), (7, 5)],
)
def test_钥匙钝化随使用次数递减(已用次数: int, 预期得分: int) -> None:
    """第 1/2/3/4+ 次命中权重 1.0 / 0.7 / 0.45 / 0.25。

    尾巴不归零：效力矩阵已经在管"别复读一招"，钝化再一刀切到 0，
    12 轮里可用的分数总量就低于过线所需，谁都赢不了。
    """
    state = 对局中(used={"socratic_question": 已用次数})

    outcome = evaluate_turn(state, hit_keys=["socratic_question"], grounded=True)

    assert outcome.delta == 预期得分


@pytest.mark.parametrize(
    ("钥匙", "预期得分"),
    [
        ("socratic_question", 8),     # 18 × 0.45 × 1.0（烦躁档）
        ("anchor_real_purpose", 14),  # 25 × 0.45 × 1.2（烦躁档）
    ],
)
def test_未扎根的命中效力大打折扣(钥匙: str, 预期得分: int) -> None:
    """扎根门控堵的是照攻略复读固定句子。

    §9.2 的对照实验：关掉它，parrot 人设胜率跳到 100%。
    """
    outcome = evaluate_turn(对局中(), hit_keys=[钥匙], grounded=False)

    assert outcome.delta == 预期得分


def test_单轮判分被钳制在上限() -> None:
    """一轮内三把钥匙全中也不能一击制胜，否则 12 轮的节奏就没了。"""
    outcome = evaluate_turn(
        对局中(),
        hit_keys=["anchor_real_purpose", "socratic_question", "expose_contradiction"],
        grounded=True,
    )

    assert outcome.delta == 25


# ── 防御姿态 ──────────────────────────────────────────────────────────────


def test_责骂之后他有两轮听不进话() -> None:
    """骂过人之后对方一时听不进去，这是真的。

    它也堵死了"钥匙刷分、失误无所谓"的打法：一句难听的话会污染接下来几轮，
    而不是当轮扣完就算清。
    """
    outcome = evaluate_turn(对局中(), hit_keys=["scold"], grounded=False)

    assert outcome.delta == -4
    assert outcome.state.guard == 2


def test_防御姿态削弱钥匙效力() -> None:
    outcome = evaluate_turn(
        对局中(guard=2), hit_keys=["socratic_question"], grounded=True
    )

    assert outcome.delta == 9  # 18 × (1 − 0.25 × 2)


def test_防御姿态削到底也还剩三成() -> None:
    """不给"一句话废掉整局"的手感：还有得救，玩家才有理由继续说下去。"""
    outcome = evaluate_turn(
        对局中(guard=5), hit_keys=["socratic_question"], grounded=True
    )

    assert outcome.delta == 5  # 18 × 0.3（下限），而非 18 × (1 − 1.25)


def test_防御姿态每轮自然消退() -> None:
    outcome = evaluate_turn(对局中(guard=5), hit_keys=[], grounded=False)

    assert outcome.state.guard == 4


# ── 追问窗口 ──────────────────────────────────────────────────────────────


def test_首次跨入更高档位时露出追问窗口() -> None:
    """他第一次晃到从没到过的那一档，会有两轮的口子。"""
    outcome = evaluate_turn(
        对局中(trust=40, peak=40), hit_keys=["socratic_question"], grounded=True
    )

    assert mood_for(outcome.state.trust) is Mood.WAVERING
    assert outcome.state.window == 2


def test_窗口内乘胜追击拿到额外加成() -> None:
    """「你差的不是方向，是没在他松动的那一刻乘胜追击」——这里让那句话成真。"""
    追上了 = evaluate_turn(
        对局中(trust=45, window=2, peak=70),
        hit_keys=["socratic_question"],
        grounded=True,
    )
    没有窗口 = evaluate_turn(
        对局中(trust=45, peak=70), hit_keys=["socratic_question"], grounded=True
    )

    assert 追上了.delta == 23  # 18 × 1.25
    assert 没有窗口.delta == 18
    # 追上了就关窗，加成不能连吃两轮
    assert 追上了.state.window == 0


def test_窗口空耗到底他会重新变硬() -> None:
    outcome = evaluate_turn(对局中(trust=45, window=1, peak=70), hit_keys=[], grounded=False)

    assert outcome.state.trust == 37  # 45 − 6（错过）− 2（流失）
    assert outcome.state.window == 0
    assert outcome.state.guard == 1


def test_跨回已经到过的档位不再开窗() -> None:
    """窗口给的是"他第一次晃"，不是每次穿过阈值都送一遍。"""
    outcome = evaluate_turn(
        对局中(trust=45, peak=70), hit_keys=["socratic_question"], grounded=True
    )

    assert outcome.state.window == 0


# ── 最后一公里 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("信任度", "预期系数"),
    [(40, 1.0), (50, 1.0), (65, 0.64), (80, 0.28)],
)
def test_越接近松口推动他越难(信任度: int, 预期系数: float) -> None:
    """让他心软是便宜的，让他说出"我不转了"不是。

    这条曲线同时是平衡上的压缩器：没有它，判分只是一场速度比赛，
    说得快的人第 5 轮就过线，说得慢的人永远够不着，中间没有过渡带。
    """
    assert resistance(信任度) == pytest.approx(预期系数)


# ── 时间的压力 ────────────────────────────────────────────────────────────


def test_未命中任何钥匙时只吃信任流失() -> None:
    outcome = evaluate_turn(new_game(), hit_keys=[], grounded=False)

    assert outcome.state.trust == 30  # 开局 32，无条件流失 −2
    # delta 只记判分结果；这一轮的下降完全来自信任流失，判分为 0
    assert outcome.delta == 0


@pytest.mark.parametrize("轮次", [3, 6, 9, 12])
def test_王老师每三轮在群里催一遍(轮次: int) -> None:
    """流失原本是隐形的，玩家看不见也不知道自己在跟什么赛跑。

    每 3 轮让它在剧情里现身一次——同一个事实，判分、台词、对话旁白三处都看得见。
    """
    assert under_pressure(轮次)

    outcome = evaluate_turn(对局中(round=轮次 - 1), hit_keys=[], grounded=False)

    assert outcome.pressured
    assert outcome.state.trust == 35  # 40 − 2（流失）− 3（施压）


def test_非施压轮只吃常规流失() -> None:
    outcome = evaluate_turn(对局中(round=3), hit_keys=[], grounded=False)

    assert not outcome.pressured
    assert outcome.state.trust == 38


@pytest.mark.parametrize(
    ("初始信任", "预期信任"),
    [(15, 14), (14, 14), (10, 10)],
)
def test_信任流失不会把人压到地板以下(初始信任: int, 预期信任: int) -> None:
    """光靠时间流逝不该把玩家踢出局——只会骂人的玩家也要能玩满 12 轮。"""
    outcome = evaluate_turn(对局中(trust=初始信任), hit_keys=[], grounded=False)

    assert outcome.state.trust == 预期信任


def test_主动失误仍能击穿地板() -> None:
    """地板保护的是流失，不是失误。被拉黑必须是玩家自己作出来的。"""
    outcome = evaluate_turn(对局中(trust=15), hit_keys=["scold"], grounded=False)

    assert outcome.state.trust == 10  # 15 − 4（责骂）− 1（流失被地板削到 −1）


def test_失误扣分不受钝化与扎根影响() -> None:
    """责骂第四次仍然照扣 −4：钝化是给钥匙的优待，不是给失误的赦免。"""
    outcome = evaluate_turn(
        对局中(used={"scold": 3}), hit_keys=["scold"], grounded=False
    )

    assert outcome.delta == -4
    # 失误不进钝化计数表
    assert outcome.state.used["scold"] == 3


# ── 蓄势池 ────────────────────────────────────────────────────────────────


def test_前三轮封顶但超出部分存入蓄势池() -> None:
    """开局就打满的玩家不该被白白浪费掉分数。

    若前 3 轮是硬上限，真懂反诈的人反而吃亏、胡聊的人赢得更快——
    规则会把"懂行"变成惩罚。超出部分入池，稍后逐轮释放。
    """
    outcome = evaluate_turn(
        new_game(), hit_keys=["anchor_real_purpose"], grounded=True
    )

    assert outcome.state.trust == 50  # 32 + 25 − 2 = 55，封顶到 50
    assert outcome.state.pool == 5    # 溢出的 5 分没有蒸发


def test_蓄势池有上限() -> None:
    """没有上限，开局猛攻的玩家能存下够直接冲线的分数。

    那样蓄势池就从"别浪费懂行的人"变成了唯一的胜负手：第 5 轮靠存款过线，
    中盘无事发生。
    """
    outcome = evaluate_turn(
        对局中(round=0, trust=45),
        hit_keys=["anchor_real_purpose", "socratic_question", "expose_contradiction"],
        grounded=True,
    )

    assert outcome.state.pool == 10


def test_第四轮起蓄势池逐轮释放() -> None:
    outcome = evaluate_turn(
        对局中(round=3, trust=50, pool=10), hit_keys=[], grounded=False
    )

    assert outcome.state.trust == 58  # 50 − 2（流失）+ 10（释放）
    assert outcome.state.pool == 0


# ── 边界与结局 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("初始信任", "命中", "预期信任"),
    [
        (95, ["expose_contradiction"], 100),
        (2, ["scold"], 0),
    ],
)
def test_信任度始终落在合法区间(
    初始信任: int, 命中: list[str], 预期信任: int
) -> None:
    outcome = evaluate_turn(对局中(trust=初始信任), hit_keys=命中, grounded=True)

    assert outcome.state.trust == 预期信任


@pytest.mark.parametrize(
    ("轮次", "初始信任", "命中", "预期结局"),
    [
        # 信任度达标即劝住，不必等轮次耗尽。
        # 用 expose：他已经松动到 78，这时候能推动他的只剩拆矛盾（见效力矩阵）
        (4, 78, ["expose_contradiction"], Ending.PERSUADED),
        # 归零即被拉黑，对局提前终止
        (5, 2, ["scold"], Ending.BLACKLISTED),
        # 第 12 轮结束仍未达标：落哪一档看他最后停在哪个档位
        (11, 40, [], Ending.TRANSFERRED),
        (11, 70, [], Ending.INTERCEPTED),
        # 对局仍在进行
        (5, 40, [], None),
    ],
)
def test_结局判定(
    轮次: int, 初始信任: int, 命中: list[str], 预期结局: Ending | None
) -> None:
    state = 对局中(round=轮次, trust=初始信任)

    outcome = evaluate_turn(state, hit_keys=命中, grounded=True)

    assert outcome.ending is 预期结局


@pytest.mark.parametrize(
    ("信任度", "预期结局"),
    [
        # 阶梯不另立阈值：档位本来就是"他有多信你"的分层，量的是同一件事
        (79, Ending.INTERCEPTED),   # 松动，只差最后一步
        (65, Ending.INTERCEPTED),
        (64, Ending.STALLED),       # 动摇：他不急着现在转，但也没被说服
        (45, Ending.STALLED),
        (44, Ending.TRANSFERRED),   # 烦躁：开局就在这一档，十二轮什么也没发生
        (1, Ending.TRANSFERRED),
    ],
)
def test_轮次耗尽时结局按情绪档位分四档(
    信任度: int, 预期结局: Ending
) -> None:
    assert decide_ending(信任度, MAX_ROUNDS) is 预期结局


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


def test_同轮多把钥匙时不下发效力倍率() -> None:
    """"这一招值多少倍"必须有唯一答案，没有就别说。

    分类器的消歧规则限定每轮最多记一把钥匙，所以这是护栏而非常规路径。
    原先取的是循环里最后一把——模型不听话多标一把时，复盘会理直气壮地
    显示一个错的倍率。少说一行好过说错一行。
    """
    state = new_game()

    一把 = evaluate_turn(state, hit_keys=["anchor_real_purpose"], grounded=True)
    两把 = evaluate_turn(
        state, hit_keys=["anchor_real_purpose", "expose_contradiction"], grounded=True
    )

    assert 一把.efficacy == EFFICACY["anchor_real_purpose"][Mood.IRRITATED]
    assert 两把.efficacy is None
    assert 两把.delta > 0, "分照加，只是倍率没法归到某一把头上"


def test_信任流失与蓄势池释放都要下发() -> None:
    """**不下发，复盘上的账就对不上。**

    玩家看到「第 3 轮 23 分，第 4 轮 +18」，结果却是 39，他会去算 23+18=41。
    差的那 2 分是信任流失，而它在界面上一个字都没有——这是真实收到的反馈。

    更要紧的是信任流失就是这局的核心张力（他背后有人在往回拉）。
    藏起来，等于把玩家在跟什么赛跑这件事也一起藏了。
    """
    平轮 = evaluate_turn(对局中(round=3), hit_keys=["socratic_question"], grounded=True)
    催单轮 = evaluate_turn(对局中(round=2), hit_keys=[], grounded=False)

    assert 平轮.drift == DRIFT
    # 第 3 轮王老师催了一遍，多掉 3 分——这一下以前在复盘里完全看不见
    assert 催单轮.pressured is True
    assert 催单轮.drift == DRIFT + PRESSURE_DRIFT

    # 账必须能对上：判分 + 流失 + 池子释放 = 信任度的净变化
    for out, before in ((平轮, 40), (催单轮, 40)):
        assert out.state.trust == before + out.delta + out.drift + out.released


def test_错过追问窗口的扣分并进流失一起下发() -> None:
    """对玩家来说它们是同一件事："这一轮没挣到分，还倒退了这么多"。

    它为什么倒退，由 window_result 那一行单独解释，不必在数字上再拆一次。
    """
    out = evaluate_turn(对局中(window=1), hit_keys=[], grounded=False)

    assert out.window_result == "missed"
    assert out.drift == DRIFT + WINDOW_MISSED_TRUST
    assert out.state.trust == 40 + out.delta + out.drift + out.released
