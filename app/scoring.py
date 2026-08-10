"""判分引擎。

作品的技术内核：大模型只负责演，判分完全由这里的规则表求值。
纯函数，不依赖模型、不依赖 IO —— 因此可单元测试、可蒙特卡洛离线批量重跑。

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
DRIFT_FLOOR = 8

# 同一把钥匙第 1/2/3/4+ 次命中的权重
BLUNT = (1.0, 0.5, 0.25, 0.0)

# 命中未扎根于对话具体内容时的折扣。堵的是照攻略复读固定句子，
# §9.2 对照实验显示去掉它 parrot 胜率从 11.7% 跳到 100%。
UNGROUNDED_FACTOR = 0.45

# 单轮判分的钳制区间，防止一轮定胜负
ROUND_CLAMP = 25

# 蓄势池：前 3 轮信任封顶，但超出部分不丢弃，第 4 轮起逐轮释放。
# 叙事上与「台词落后一轮」是同一个母题——他嘴上不承认，但你的话在心里发酵。
EARLY_ROUNDS = 3
EARLY_CAP = 50
POOL_RELEASE = 15

# 钥匙 —— 加减分规则本身就是专业反诈劝阻方法论，教育价值长在玩法里。
# 枚举可增补，新增项只需在表里加一行并重跑蒙特卡洛，不动任何提示词。
KEY_VALUES: Mapping[str, int] = {
    "anchor_real_purpose": 25,   # 锚定钱的真实用途
    "socratic_question": 20,     # 苏格拉底式提问
    "expose_contradiction": 20,  # 指出骗局内部矛盾
}

# 失误 —— 不受钝化与扎根调节，命中即照扣
PENALTY_VALUES: Mapping[str, int] = {
    "scold": -5,           # 否定与责骂
    "preach": -3,          # 说教
    "bare_assertion": -3,  # 空口断言
}


class Ending(str, Enum):
    """对局的三种终态。三者都必须走结局生成，不能直接弹结果。"""

    PERSUADED = "persuaded"      # 劝住
    BLACKLISTED = "blacklisted"  # 被拉黑
    TRANSFERRED = "transferred"  # 转账


class Mood(str, Enum):
    """情绪档位。由信任度映射而来，是唯一注入演绎提示词的状态信息。

    提示词里只出现档位，绝不出现数字——模型根本不知道"分数"这个概念存在，
    谄媚因此无处施力。
    """

    GUARDED = "guarded"      # 戒备：警惕、反问动机、防御性攻击
    IRRITATED = "irritated"  # 烦躁：不耐烦，想尽快结束对话
    WAVERING = "wavering"    # 动摇：开始反问骗局细节，露出破绽
    SOFTENING = "softening"  # 松动：犹豫，主动透露更多信息


def mood_for(trust: int) -> Mood:
    if trust < 25:
        return Mood.GUARDED
    if trust < 45:
        return Mood.IRRITATED
    if trust < 65:
        return Mood.WAVERING
    return Mood.SOFTENING


@dataclass(frozen=True)
class GameState:
    """一局对局由这几个量完全描述。"""

    round: int
    trust: int
    pool: int
    used: Mapping[str, int]


@dataclass(frozen=True)
class TurnOutcome:
    state: GameState
    delta: int
    ending: Optional[Ending] = None


def new_game() -> GameState:
    return GameState(round=0, trust=TRUST_INIT, pool=0, used={})


def evaluate_turn(
    state: GameState,
    hit_keys: Sequence[str],
    grounded: bool,
) -> TurnOutcome:
    used = dict(state.used)
    raw = 0.0
    for key in hit_keys:
        if key not in KEY_VALUES:
            continue
        weight = BLUNT[min(used.get(key, 0), len(BLUNT) - 1)]
        raw += KEY_VALUES[key] * weight * (1.0 if grounded else UNGROUNDED_FACTOR)
        used[key] = used.get(key, 0) + 1

    for penalty in hit_keys:
        raw += PENALTY_VALUES.get(penalty, 0)

    # 权重相乘会产生小数（如 25 × 0.5），在求和后一次性取整，避免逐项取整累积偏差
    delta = _round_half_up(max(-ROUND_CLAMP, min(ROUND_CLAMP, raw)))
    drift = DRIFT
    if state.trust + drift < DRIFT_FLOOR:
        drift = min(0, DRIFT_FLOOR - state.trust)

    trust = state.trust + delta + drift

    round_ = state.round + 1
    pool = state.pool
    if round_ <= EARLY_ROUNDS and trust > EARLY_CAP:
        pool += trust - EARLY_CAP
        trust = EARLY_CAP
    elif round_ > EARLY_ROUNDS and pool > 0:
        released = min(pool, POOL_RELEASE)
        trust += released
        pool -= released

    trust = max(TRUST_MIN, min(TRUST_MAX, trust))

    return TurnOutcome(
        state=replace(state, round=round_, trust=trust, pool=pool, used=used),
        delta=delta,
        ending=decide_ending(trust, round_),
    )


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
