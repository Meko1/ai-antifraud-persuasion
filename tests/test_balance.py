"""平衡验证：蒙特卡洛。

判分脱离模型，因此可以离线、批量、瞬间重跑——这正是"游戏平衡从玄学变成
求解"的前提。本文件把 §9.4 的数值门槛钉进 CI。

门槛守的是**区间**（§9.4），不是 §9.1 表里那个 37.4% 的点：人设的命中概率
是重建的，点估计对不上不算错，落出区间才算。
"""

import random

import pytest

from app import scoring
from app.scenario import SCENARIOS
from app.scoring import KEY_VALUES, Ending, Mood
from tools.balance_sim import (
    BLACKLIST_CEILING,
    PARROT,
    PARROT_WIN_CEILING,
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
    """§9.1 speedrun 人设，平均轮数 5–6。

    这条钉的是**一整条求值链**：会读人的玩家每一轮挑对档位、蓄势池按时释放、
    最后一公里把冲线那一下压住。任何一环改了它都会红，这正是它的用处。

    七把钥匙上线后的实际走法（seed=0，每一步都对得上）：
      R1 烦躁  anchor      +25 → 57 封顶 50，入池 5
      R2 动摇  expose      +25 → 73 封顶 50，入池 10（池子有上限）
      R3 动摇  check       +22 → 70 封顶 50，入池已满
      R4 动摇  socratic    +18，释放 10 → 76
      R5 松动  expose       +6（阻力曲线把 32 压成 6）→ 80，劝住

    **R5 那一下最能说明问题**：同一把 expose，R2 值 25，R5 只值 6。
    让他心软是便宜的，让他说出"我不转了"不是。
    """
    result = play_game(SPEEDRUN, rng=random.Random(0))

    assert result.ending is Ending.PERSUADED
    assert result.rounds == 5


def test_验收门槛全部通过() -> None:
    """§9.4 六条门槛，数字以 `tools/balance_sim.py` 里的常量为准。

    改动判分参数、增补钥匙、调整取整方向——任何一项动了都要在这里复核。
    这条测试**只调用 `check_thresholds`，不复述任何数字**：门槛在模块里
    改一次就够，测试跟着走。此前这段文档写着"胜率 30–40%、被拉黑 < 10%"，
    而模块里早已是 12–22% 与 12%——一份说明抄两处，迟早对不上。
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

    assert 有门控.win_rate < 0.15
    # 关掉门控，复读固定句子的胜率**击穿 §9.4 那条 15% 的上限，而且是数量级地穿**。
    #
    # 绝对值一路在降：90% → 60% → 现在 31%。降的原因每次都一样——效力矩阵、
    # 阻力曲线、收紧后的钝化各自又多拦掉一点复读。**所以这条断言盯的是倍数
    # 而不是绝对值**：绝对值会随每次平衡迭代往下走，而"门控没了就报废"
    # 这件事由倍数说了算。写死 0.60 只会让这条测试每次调参都要改一遍数字。
    assert 无门控.win_rate > PARROT_WIN_CEILING * 2
    assert 无门控.win_rate > 有门控.win_rate * 10


def test_没有人会在第六轮就被踢出局() -> None:
    """novice 也要能平均玩到 11 轮——这直接保护投票转化率。

    作品要奖励方法论，不是刁难玩家；中途出局的人不会回来投票。

    ### 这个数一路在涨，下一个人请盯着它

    加权被拉黑率：**5.2%（8-17 前）→ 7.3%（加合规红线）→ 10.5%（加四把钥匙）**，
    novice 单独看是 **20.8% → 29.1% → 41.4%**。两次都不是 bug：每加一类
    "说错话的方式"，说错话的人就更容易出局。而 novice 本来就贴着地板在走，
    多一次 −2 就够把一局推过线。

    第二次那 11 个百分点**全部**来自「有据告知」说早了那条轴（实测：把
    `warning_rate` 归零，novice 立刻回到 30.2%）。试过两种减轻办法，都没用：
    说早了不顶防备只买回 0.4 个百分点，收紧钝化只买回 1 个。
    真正的成本是**那一轮的钥匙收入没了**，这是这个机制的定义，去不掉。

    所以门槛从 0.10 放到 §9.4 那条 12%（`BLACKLIST_CEILING`），
    不再单独立一个更严的数——两处守同一件事却给两个数字，
    早晚会出现"模块里绿了、测试里红了"的对不上。
    **但 12% 是硬顶：再加一类失误之前，先来看这条。**
    """
    results = run_all(GAMES, SEED)

    assert results["novice"].mean_rounds > 10.0
    assert weighted(results, "blacklist_rate") < BLACKLIST_CEILING


def test_失误值取半分对总体胜率的影响不超过一个百分点(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失误值必须是整数，但整数化不能把平衡推走。

    现在的 −4/−2/−2 已经是整数，所以这条测的是**灵敏度**：
    在标定点附近挪半分，总体胜率是否稳得住。挪半分就翻车的参数，
    在真人对局的噪声下同样守不住。
    """
    标定值 = weighted(run_all(GAMES, SEED), "win_rate")

    monkeypatch.setattr(
        scoring,
        "PENALTY_VALUES",
        {"scold": -4.5, "preach": -2.5, "bare_assertion": -2.5},
    )
    挪半分 = weighted(run_all(GAMES, SEED), "win_rate")

    assert abs(标定值 - 挪半分) < 0.01


# ── 多场景（POSITIONING 第 5 步）─────────────────────────────────────────


@pytest.mark.parametrize("场景", SCENARIOS, ids=lambda s: s.id)
def test_每个场景各自都要过门槛(场景) -> None:
    """难度不是"整体平均下来还行"，是**每一个场景单独都得站得住**。

    一个场景太难、另一个太水，平均起来漂亮——而玩家一次只玩一个。
    """
    assert check_thresholds(run_all(GAMES, SEED, scene=场景)) == []


def test_换了场景最优解必须跟着变() -> None:
    """**这是第二个场景存在的唯一理由**，也是 POSITIONING 那三条可检验判据里
    最后一条（"能力可迁移，不是背下一个剧本"）唯一能被直接验证的地方。

    判据写死在 POSITIONING「路线」第 5 步：换场景之后效力矩阵必须翻过来。
    两个荐股场景证明不了任何事——那只是换了套皮，玩家背下的还是同一份清单。

    实际结果（`python -m tools.balance_sim` 末尾会印出来）：

        档位      chen                  zhou
        戒备      锚定真实用途           指出内部矛盾
        烦躁      锚定真实用途           有据告知
        动摇      指出内部矛盾           确认理解
        松动      指出内部矛盾           锚定真实用途

    四档全不一样。老陈是贪：先把抽象收益拉回具体代价，等他松动了再拆话术。
    周淑琴是怕：先用可核验的事实拆掉"他是警察"，最后她不抖了，
    "这是你的养老钱"才说得进去。**同一张判分表，反过来的两条弧线。**

    这里只要求"多数档位不同"而不是"全部不同"：全部不同是现在的实际情况，
    但把它钉死会让以后微调某一格时收到一条无关的红灯。
    """
    picks = {
        scene.id: [
            max(KEY_VALUES, key=lambda k: KEY_VALUES[k] * scene.efficacy[k][mood])
            for mood in (Mood.GUARDED, Mood.IRRITATED, Mood.WAVERING, Mood.SOFTENING)
        ]
        for scene in SCENARIOS
    }
    chen, zhou = picks["chen"], picks["zhou"]

    不同 = sum(a != b for a, b in zip(chen, zhou))
    assert 不同 >= 3, f"两个场景的最优解几乎一样，第二个场景没有存在的必要：{picks}"


@pytest.mark.parametrize("场景", SCENARIOS, ids=lambda s: s.id)
def test_场景不许自己长出一把万能钥匙(场景) -> None:
    """同一把钥匙在四个档位全是最优 = 效力矩阵在这个场景里白写了。

    玩法会当场退化成过清单：认准那一把，从头用到尾。
    zhou 的第一版就栽在这儿——`expose_contradiction` 四档通吃，
    表面上"和 chen 不一样"，实际上比 chen 还退化。
    """
    best = {
        max(KEY_VALUES, key=lambda k: KEY_VALUES[k] * 场景.efficacy[k][mood])
        for mood in (Mood.GUARDED, Mood.IRRITATED, Mood.WAVERING, Mood.SOFTENING)
    }

    assert len(best) >= 2, f"[{场景.id}] 四个档位的最优解都是同一把：{best}"
