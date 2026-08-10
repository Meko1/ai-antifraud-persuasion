"""平衡验证：蒙特卡洛。

判分脱离模型，因此可以离线、批量、瞬间重跑——这正是"游戏平衡从玄学变成
求解"的前提。本文件把 §9.4 的数值门槛钉进 CI。

门槛守的是**区间**（§9.4），不是 §9.1 表里那个 37.4% 的点：人设的命中概率
是重建的，点估计对不上不算错，落出区间才算。
"""

import random

import pytest

from app import scoring
from app.scoring import Ending
from tools.balance_sim import (
    PARROT,
    SEED,
    SPEEDRUN,
    check_thresholds,
    play_game,
    run_all,
    simulate,
    weighted,
)

# CI 里跑 5000 局／人设（约 2 秒）。种子固定，结果完全可复现——
# 蒙特卡洛入 CI 的前提是它不能偶尔变红。
GAMES = 5000


def test_完美执行的玩家在第五轮劝住() -> None:
    """§9.1 speedrun 人设，平均轮数 5.0。

    这条是手算出来的独立真值，不是从代码推的：
      R1 anchor  +25 → 55 封顶 50，入池 5
      R2 socratic +20 → 68 封顶 50，入池 23
      R3 expose  +20 → 68 封顶 50，入池 41
      R4 anchor ×0.5 → +13，61，释放 15 → 76
      R5 socratic ×0.5 → +10，84 ≥ 80，劝住
    """
    result = play_game(SPEEDRUN, rng=random.Random(0))

    assert result.ending is Ending.PERSUADED
    assert result.rounds == 5


def test_验收门槛全部通过() -> None:
    """§9.4：加权总体胜率 30–40%、parrot < 20%、整体被拉黑率 < 10%。

    改动判分参数、增补钥匙、调整取整方向——任何一项动了都要在这里复核。
    §3.5 的脚注特别点名：失误值由 −5/−2.5/−2.5 取整为 −5/−3/−3，
    取整方向略偏严，须在 CI 中复核。
    """
    results = run_all(GAMES, SEED)

    assert check_thresholds(results) == []


def test_扎根门控是必需品而非优化项() -> None:
    """§9.2 对照实验：同一套参数，仅关闭扎根门控。

    这是扎根门控存在的全部理由——攻略一旦在工作群里传开，
    没有门控的话游戏当天就报废。
    """
    有门控 = simulate(PARROT, GAMES, SEED, grounding_gate=True)
    无门控 = simulate(PARROT, GAMES, SEED, grounding_gate=False)

    assert 有门控.win_rate < 0.20
    assert 无门控.win_rate > 0.90


def test_没有人会在第六轮就被踢出局() -> None:
    """novice 也要能平均玩到 11 轮——这直接保护投票转化率。

    作品要奖励方法论，不是刁难玩家；中途出局的人不会回来投票。
    """
    results = run_all(GAMES, SEED)

    assert results["novice"].mean_rounds > 10.0
    assert weighted(results, "blacklist_rate") < 0.10


def test_失误值取整对总体胜率的影响不超过一个百分点(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3.5 脚注点名要求复核的那一条。

    失误值的蒙特卡洛标定点是 −5/−2.5/−2.5，参数表取整为 −5/−3/−3。
    取整方向略偏严，脚注预期影响 <1pp——这里把"预期"变成"验证"。
    """
    取整后 = weighted(run_all(GAMES, SEED), "win_rate")

    monkeypatch.setattr(
        scoring,
        "PENALTY_VALUES",
        {"scold": -5, "preach": -2.5, "bare_assertion": -2.5},
    )
    标定点 = weighted(run_all(GAMES, SEED), "win_rate")

    assert abs(取整后 - 标定点) < 0.01
