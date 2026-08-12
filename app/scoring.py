"""判分引擎。

作品的技术内核：大模型只负责演，判分完全由这里的规则表求值。
纯函数，不依赖模型、不依赖 IO —— 因此可单元测试、可蒙特卡洛离线批量重跑。

核心命题：**同一句话在不同时机值不同的分**。三把钥匙在任何时候等值时，
最优解是循环使用，玩法退化成过清单；一旦按情绪档位分化，玩家就必须先读人。
参数由蒙特卡洛标定，见 docs/TECH-DESIGN.md §3.5 与 §9。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping, Optional, Sequence

TRUST_INIT = 32
TRUST_MIN = 0
TRUST_MAX = 100

MAX_ROUNDS = 12
WIN_THRESHOLD = 80
BLACKLIST_THRESHOLD = 0

# 每轮无条件的信任流失 —— 劝阻对象仍在持续接受骗局一方的洗脑。
# 地板保护确保光靠流失压不到拉黑线：只有主动失误才会把人推下去。
DRIFT = -2
DRIFT_FLOOR = 14

# 李老师的反向施压。流失是隐形的，玩家看不见也不知道自己在跟什么赛跑；
# 每 3 轮让它在剧情里现身一次——群里又催了一遍，该轮额外多掉 3 分。
# 这不是新增难度，是把一个已经存在的数值搬到台面上。
PRESSURE_EVERY = 3
PRESSURE_DRIFT = -3

# 同一把钥匙第 1/2/3/4+ 次命中的权重。
# 尾巴不再归零：效力矩阵已经承担了"别复读一招"的职责（用错档位直接打三折），
# 钝化再一刀切到 0，12 轮里可用的分数总量就低于过线所需，谁都赢不了。
BLUNT = (1.0, 0.7, 0.45, 0.25)

# 命中未扎根于对话具体内容时的折扣。堵的是照攻略复读固定句子，
# §9.2 对照实验显示去掉它 parrot 胜率从 11.7% 跳到 100%。
UNGROUNDED_FACTOR = 0.45

# 单轮判分的钳制区间。分类器每轮最多记一把钥匙（见 CLASSIFY_SYSTEM_PROMPT
# 的消歧规则 1），所以现在很难顶到它——它留作护栏，不是常规路径。
ROUND_CLAMP = 25

# 蓄势池：前 3 轮信任封顶，但超出部分不丢弃，第 4 轮起逐轮释放。
# 叙事上与「台词落后一轮」是同一个母题——他嘴上不承认，但你的话在心里发酵。
EARLY_ROUNDS = 3
EARLY_CAP = 50
POOL_RELEASE = 12
# 池子有上限。没有它，开局猛攻的玩家能存下够直接冲线的分数，蓄势池就从
# "别浪费懂行的人"变成了唯一的胜负手——第 5 轮靠存款过线，中盘无事发生。
POOL_CAP = 10

# 钥匙 —— 加减分规则本身就是专业反诈劝阻方法论，教育价值长在玩法里。
# 枚举可增补，新增项只需在表里加一行并重跑蒙特卡洛，不动任何提示词。
#
# 基值经蒙特卡洛回归标定。方案里建议下调到 14/10/13，实测那组过低：配上效力
# 矩阵、防御姿态与阻力曲线之后，12 轮里能挣到的分数总量顶不到过线所需，
# 连完美执行的玩家也只能靠蓄势池过线。真正变了的是**相对关系**——
# 三把钥匙不再等值，而且每一把都要乘以档位效力。
KEY_VALUES: Mapping[str, int] = {
    "anchor_real_purpose": 25,   # 锚定钱的真实用途
    "socratic_question": 18,     # 苏格拉底式提问
    "expose_contradiction": 23,  # 指出骗局内部矛盾
}

# 失误 —— 不受钝化、扎根与档位调节，命中即照扣。
# 失误另有防御姿态那层后果（见 GUARD_PENALTY），当轮扣分因此不必给得太重。
PENALTY_VALUES: Mapping[str, int] = {
    "scold": -4,           # 否定与责骂
    "preach": -2,          # 说教
    "bare_assertion": -2,  # 空口断言
}


class Ending(str, Enum):
    """对局的三种终态。三者都必须走结局生成，不能直接弹结果。"""

    PERSUADED = "persuaded"      # 劝住
    BLACKLISTED = "blacklisted"  # 被拉黑
    TRANSFERRED = "transferred"  # 转账


class Mood(str, Enum):
    """情绪档位。由信任度映射而来，是唯一注入演绎提示词的状态信息。

    提示词里只出现档位，绝不出现数字——模型根本不知道"分数"这个概念存在，
    谄媚因此无处施力。对局中前端同样只显示档位词。
    """

    GUARDED = "guarded"      # 戒备：警惕、反问动机、防御性攻击
    IRRITATED = "irritated"  # 烦躁：不耐烦，想尽快结束对话
    WAVERING = "wavering"    # 动摇：开始反问骗局细节，露出破绽
    SOFTENING = "softening"  # 松动：犹豫，主动透露更多信息


# 档位的高低次序。用于判断"首次跨入更高一档"（见追问窗口）。
MOOD_ORDER: Mapping[Mood, int] = {
    Mood.GUARDED: 0,
    Mood.IRRITATED: 1,
    Mood.WAVERING: 2,
    Mood.SOFTENING: 3,
}


def mood_for(trust: int) -> Mood:
    if trust < 25:
        return Mood.GUARDED
    if trust < 45:
        return Mood.IRRITATED
    if trust < 65:
        return Mood.WAVERING
    return Mood.SOFTENING


# ── 钥匙 × 情绪档位 效力矩阵 ───────────────────────────────────────────────
#
# 这张表是整个玩法的支点：它把"背下三把钥匙"变成"判断现在该用哪一把"。
#
# · 锚定真实用途是开场。他敌意最重的时候，问"这钱本来干什么用的"才是换取
#   "被听进去的资格"的那一招；等他松动了再问就多余了，他自己已经说了。
# · 指出内部矛盾只在他已经开始晃的时候管用。太早用，他会替骗局辩护，
#   反而更 entrenched——所以除了折扣，还额外顶起防御姿态（见 GUARD_MISTIMED）。
#   这是本作最想让玩家学到的一条真东西。
# · 苏格拉底式提问是连接件：全档位通用，但基值最低。
EFFICACY: Mapping[str, Mapping[Mood, float]] = {
    "anchor_real_purpose": {
        Mood.GUARDED: 1.3, Mood.IRRITATED: 1.2,
        Mood.WAVERING: 0.8, Mood.SOFTENING: 0.6,
    },
    "socratic_question": {
        Mood.GUARDED: 1.0, Mood.IRRITATED: 1.0,
        Mood.WAVERING: 1.0, Mood.SOFTENING: 1.0,
    },
    "expose_contradiction": {
        Mood.GUARDED: 0.3, Mood.IRRITATED: 0.6,
        Mood.WAVERING: 1.3, Mood.SOFTENING: 1.4,
    },
}

# ── 防御姿态 ──────────────────────────────────────────────────────────────
#
# 骂过人之后对方一时听不进去，这是真的。它也堵死了"钥匙刷分、失误无所谓"
# 的打法：一句难听的话会污染接下来两三轮，而不是当轮扣完就算清。
GUARD_PENALTY = 2      # 每命中一次失误
GUARD_MISTIMED = 1     # 在戒备/烦躁档位硬拆矛盾
GUARD_DECAY = 1        # 每轮结算后自然消退
GUARD_STEP = 0.25      # 每一点防御姿态削掉的钥匙效力
GUARD_FLOOR = 0.3      # 削到底也还剩三成——不给"一句话废掉整局"的手感

# ── 追问窗口 ──────────────────────────────────────────────────────────────
#
# 「你差的不是方向，是没在他松动的那一刻乘胜追击」——这句复盘文案原本只是
# 文案，这个机制让它变成真的：他第一次晃到更高一档时会露出两轮的口子，
# 追上了额外加成，没追上他重新变硬。
WINDOW_ROUNDS = 2
WINDOW_BONUS = 1.25
WINDOW_MISSED_TRUST = -6

# ── 最后一公里 ────────────────────────────────────────────────────────────
#
# 让他心软是便宜的，让他说出"我不转了"不是。真实的劝阻里，同情、动摇、
# 甚至承认你说得有道理，都远远早于那个决定——最后那一步他要推翻的是自己
# 三个月来的全部投入。所以越接近松口，同样一句话推动他的幅度越小。
#
# 这条曲线同时是平衡上的压缩器：没有它，判分只是一场速度比赛，
# 说得快的人第 5 轮就过线，说得慢的人永远够不着，中间没有过渡带。
RESIST_FROM = 50
RESIST_FLOOR = 0.28


@dataclass(frozen=True)
class GameState:
    """一局对局由这几个量完全描述。"""

    round: int
    trust: int
    pool: int
    used: Mapping[str, int]
    guard: int = 0     # 防御姿态，削弱钥匙效力
    window: int = 0    # 追问窗口的剩余轮数
    peak: int = TRUST_INIT  # 历史最高信任度，用来判断"首次跨入更高一档"


@dataclass(frozen=True)
class TurnOutcome:
    state: GameState
    delta: int
    ending: Optional[Ending] = None
    # 本轮李老师是否在群里又催了一遍。前端把它渲染成一条剧情旁白，
    # 演绎提示词也据此加压——同一个事实，三个地方看得见。
    pressured: bool = False


def new_game() -> GameState:
    return GameState(round=0, trust=TRUST_INIT, pool=0, used={})


def under_pressure(round_: int) -> bool:
    """第 round_ 轮李老师是否又催了一遍。

    演绎提示词要在判分之前就知道这件事，判分自己也要用——所以判据只此一份。
    """
    return round_ % PRESSURE_EVERY == 0


def evaluate_turn(
    state: GameState,
    hit_keys: Sequence[str],
    grounded: bool,
) -> TurnOutcome:
    """求值一轮。顺序见 docs/TECH-DESIGN.md §3.4，改动顺序等于改动平衡。"""
    # 用【回合开始时】的档位。它与注入演绎提示词的那一档是同一个，
    # 于是"他现在是什么状态"对玩家和对判分是同一件事（ADR-0002）。
    mood = mood_for(state.trust)
    guard_factor = max(GUARD_FLOOR, 1 - GUARD_STEP * state.guard)
    window_factor = WINDOW_BONUS if state.window > 0 else 1.0
    ground_factor = 1.0 if grounded else UNGROUNDED_FACTOR
    resist_factor = resistance(state.trust)

    used = dict(state.used)
    raw = 0.0
    hit_key = False
    for key in hit_keys:
        if key not in KEY_VALUES:
            continue
        hit_key = True
        blunt = BLUNT[min(used.get(key, 0), len(BLUNT) - 1)]
        raw += (
            KEY_VALUES[key] * blunt * ground_factor
            * EFFICACY[key][mood] * guard_factor * window_factor * resist_factor
        )
        used[key] = used.get(key, 0) + 1

    # 失误不受任何调节：钝化是给钥匙的优待，不是给失误的赦免
    for penalty in hit_keys:
        raw += PENALTY_VALUES.get(penalty, 0)

    # 权重相乘会产生小数，在求和后一次性取整，避免逐项取整累积偏差
    delta = _round_half_up(max(-ROUND_CLAMP, min(ROUND_CLAMP, raw)))

    # 先让上一轮的防备消退，再累加本轮新顶起来的。
    # 顺序反过来的话，decay 会当场抵掉本轮那 +1，"太早拆矛盾"就一点后果都没有了。
    guard = max(0, state.guard - GUARD_DECAY)

    # 错过窗口不只是掉分，他还会重新竖起防备
    window, missed = _settle_window(state.window, hit_key)
    guard += GUARD_MISTIMED if missed else 0
    guard += GUARD_PENALTY * sum(1 for k in hit_keys if k in PENALTY_VALUES)
    if "expose_contradiction" in hit_keys and mood in (Mood.GUARDED, Mood.IRRITATED):
        guard += GUARD_MISTIMED

    round_ = state.round + 1
    pressured = under_pressure(round_)

    drift = DRIFT + (PRESSURE_DRIFT if pressured else 0)
    # 地板保护的是流失，不是失误：被拉黑必须是玩家自己作出来的。
    # 错过追问窗口算玩家的失误，因此不受地板保护。
    if state.trust + drift < DRIFT_FLOOR:
        drift = min(0, DRIFT_FLOOR - state.trust)

    trust = state.trust + delta + drift + missed

    pool = state.pool
    if round_ <= EARLY_ROUNDS and trust > EARLY_CAP:
        pool = min(POOL_CAP, pool + trust - EARLY_CAP)
        trust = EARLY_CAP
    elif round_ > EARLY_ROUNDS and pool > 0:
        released = min(pool, POOL_RELEASE)
        trust += released
        pool -= released

    trust = max(TRUST_MIN, min(TRUST_MAX, trust))

    # 他第一次晃到从没到过的那一档，口子就开了
    peak = max(state.peak, trust)
    if window == 0 and _crossed_up(state.peak, trust):
        window = WINDOW_ROUNDS

    return TurnOutcome(
        state=replace(
            state,
            round=round_,
            trust=trust,
            pool=pool,
            used=used,
            guard=guard,
            window=window,
            peak=peak,
        ),
        delta=delta,
        ending=decide_ending(trust, round_),
        pressured=pressured,
    )


def resistance(trust: int) -> float:
    """越接近松口，同一句话的推动力越小。RESIST_FROM 以下不打折。"""
    if trust <= RESIST_FROM:
        return 1.0
    span = WIN_THRESHOLD - RESIST_FROM
    decayed = 1.0 - (1.0 - RESIST_FLOOR) * (trust - RESIST_FROM) / span
    return max(RESIST_FLOOR, decayed)


def _settle_window(window: int, hit_key: bool) -> tuple[int, int]:
    """结算追问窗口，返回（剩余轮数，信任度惩罚）。

    追上了就关窗（加成已经在 window_factor 里给过）；空耗到底他重新变硬。
    """
    if window <= 0:
        return 0, 0
    if hit_key:
        return 0, 0
    window -= 1
    return window, WINDOW_MISSED_TRUST if window == 0 else 0


def _crossed_up(peak: int, trust: int) -> bool:
    """本轮是否首次跨入 动摇/松动 中此前没到过的那一档。"""
    now = mood_for(trust)
    if now not in (Mood.WAVERING, Mood.SOFTENING):
        return False
    return MOOD_ORDER[now] > MOOD_ORDER[mood_for(peak)]


def decide_ending(trust: int, round_: int) -> Optional[Ending]:
    if trust >= WIN_THRESHOLD:
        return Ending.PERSUADED
    if trust <= BLACKLIST_THRESHOLD:
        return Ending.BLACKLISTED
    if round_ >= MAX_ROUNDS:
        return Ending.TRANSFERRED
    return None


def _round_half_up(value: float) -> int:
    """四舍五入，且对负数对称（−12.5 → −13），不用 Python 内建的银行家舍入。"""
    if value >= 0:
        return math.floor(value + 0.5)
    return -math.floor(-value + 0.5)
