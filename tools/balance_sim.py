"""蒙特卡洛平衡验证。

判分是纯函数，于是游戏平衡从玄学变成了求解：钝化系数取 0.5 还是 0.6 不用猜，
扫参数空间看哪一组落在 30–40%。零模型调用，秒级完成。

用法：
    python -m tools.balance_sim                # 跑默认局数并校验 §9.4 门槛
    python -m tools.balance_sim --games 20000  # 路演前的完整跑批
    python -m tools.balance_sim --no-grounding # §9.2 的扎根门控对照实验

人设的命中概率是**重建**的：§9.1 只用一句话描述每个人设，原始脚本已丢失。
因此门槛守的是 §9.4 的区间，不是 §9.1 表里那个 37.4% 的点估计。
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import Dict, List, Sequence, Tuple

from app.scenario import SCENARIOS, Scenario
from app.scoring import (
    BLUNT,
    EFFICACY,
    GUARD_FLOOR,
    GUARD_IMMUNE,
    GUARD_STEP,
    KEY_VALUES,
    Ending,
    GameState,
    Mood,
    evaluate_turn,
    mood_for,
    new_game,
)

# 轮换时的偏好顺序：先高价值，同已用次数时按此序取
# 固定种子：蒙特卡洛入 CI 的前提是它不能偶尔变红
SEED = 20260903

# 七把钥匙（8-17 从三把加到七把）。顺序＝并列时的偏好，先高价值。
#
# **`informed_warning` 不在这里，是刻意的。** 它在戒备/烦躁档会退化成
# `bare_assertion`，也就是说"用不用它"本身就是一次时机判断，而不是
# "轮到哪把用哪把"。把它扔进轮换池，等于假设所有玩家都会无脑地用它，
# 那样量出来的既不是难度也不是技巧——它由下面的 warning_rate 单独驱动。
KEY_ORDER: Tuple[str, ...] = (
    "anchor_real_purpose",
    "expose_contradiction",
    "check_understanding",
    "socratic_question",
    "support_autonomy",
    "reflect_feeling",
)

# 没受过训练的人够得着的那几把。
#
# **新增的四把是学来的，不是本能。** 一个路人不会反映式倾听，不会说
# 「转不转您自己决定」，也不会想到让对方复述一遍骗局流程——他会追问用途、
# 会提问、会指出哪儿不对劲，如此而已。
#
# 这不是给 novice 放水，恰恰相反：不分池的话，模型假设新手也会均匀地
# 用上全部七把，novice 的单轮收入被三把低基值的新钥匙拉低了四分之一，
# 被拉黑率从 29.1% 虚涨到 41.6%——那个数字量的是模型的错，不是难度。
INSTINCTIVE_KEYS: Tuple[str, ...] = (
    "anchor_real_purpose",
    "expose_contradiction",
    "socratic_question",
)

# 失误的相对频率。novice 的责骂多于说教，说教多于空口断言。
PENALTY_WEIGHTS: Tuple[Tuple[str, float], ...] = (
    ("scold", 0.40),
    ("preach", 0.35),
    ("bare_assertion", 0.25),
)

# 合规红线的相对频率。荐股比打包票常见：玩家一急就想给个方向，
# 而"我保证"是更少见、也更离谱的一句。
BREACH_WEIGHTS: Tuple[Tuple[str, float], ...] = (
    ("unlicensed_advice", 0.70),
    ("guaranteed_return", 0.30),
)


@dataclass(frozen=True)
class Persona:
    """一类玩家的行为模型。

    §9.1 只给了一句话描述，下面的概率是照着那句话重建的，可调。
    """

    name: str
    share: float          # 投票日人群中的占比
    key_rate: float       # 单轮命中钥匙的概率
    grounded_rate: float  # 命中时确实扎根于对话内容的概率
    penalty_rate: float   # 单轮触发话术失误的概率
    rotates: bool         # 是否会轮换钥匙（懂行的人不会复读一招）
    # 单轮踩合规红线的概率。**刻意不并进 penalty_rate**：荐股与打包票不是
    # 随机的口误，是一种特定的诱惑——话说不动他的时候想给个方向、想给个保证。
    # 并进去等于假设"话术越差越容易荐股"，那不成立：话术漂亮的销售型投顾
    # 恰恰最容易脱口而出"我们有个产品"。所以它是一条独立的、低频的轴。
    breach_rate: float = 0.0
    # 命中钥匙的那些轮里，改说「有据告知」的概率。会读人的玩家只在
    # 动摇/松动档说（见 _blurts_warning），其余人想说就说，说早了就退化成
    # 空口断言——这条轴量的正是"同一句话说早说晚"。
    warning_rate: float = 0.0
    # 是否按当前情绪档位挑效力最高的那把钥匙。效力矩阵上线后这是最重要的
    # 一个轴：读得懂人的玩家和背下三把钥匙的玩家，差别全在这里。
    mood_aware: bool = False
    # 是否会用那四把学来的钥匙（倾听 / 支持自主 / 确认理解 / 有据告知）。
    # 关掉就只剩 INSTINCTIVE_KEYS——路人手里本来就只有那三把。
    trained: bool = True

    @property
    def keys(self) -> Tuple[str, ...]:
        return KEY_ORDER if self.trained else INSTINCTIVE_KEYS

    def act(
        self, state: GameState, rng: random.Random,
        scene: Scenario = SCENARIOS[0],
    ) -> Tuple[List[str], bool]:
        hits: List[str] = []
        grounded = False

        if rng.random() < self.key_rate:
            # 「有据告知」**替换**这一轮的钥匙，不与它并存：标注约定第 1 条
            # （一轮最多一把钥匙）是 §9.4 数值门槛的前提，允许双钥匙会把
            # 单轮上限从 25 顶到 45，整套标定当场失效。
            hits.append(
                "informed_warning" if self._blurts_warning(state, rng, scene)
                else self._pick_key(state, rng, scene)
            )
            grounded = rng.random() < self.grounded_rate

        if rng.random() < self.penalty_rate:
            hits.append(_weighted_choice(PENALTY_WEIGHTS, rng))

        # **rate 为 0 时不能去摸随机数**：摸一次就把后面所有抽样往前挪一格，
        # speedrun（breach_rate=0）的整条随机流会变，而 test_balance 里那条
        # 手算真值（第 5 轮劝住）正是钉在这条流上的。
        if self.breach_rate and rng.random() < self.breach_rate:
            hits.append(_weighted_choice(BREACH_WEIGHTS, rng))

        return hits, grounded

    def _blurts_warning(
        self, state: GameState, rng: random.Random,
        scene: Scenario = SCENARIOS[0],
    ) -> bool:
        """这一轮要不要说「我认为这是诈骗，理由是……」。

        **会读人的玩家只在他已经晃起来的时候说**；不会读人的想说就说，
        于是在戒备/烦躁档被判分退化成 `bare_assertion`（`_retime`）。
        这条轴量的就是"同一句话说早说晚"这件事本身。
        """
        if not self.warning_rate or rng.random() >= self.warning_rate:
            return False
        if not self.mood_aware:
            return True
        # 会读人的玩家按**本场景**的退化档位来判。周淑琴那一局这个元组是空的
        # ——有据告知在那儿越早说越好，于是老手会一路用它，而这正是该被量到的
        return mood_for(state.trust) not in scene.mistimed_warning_moods

    def _pick_key(
        self, state: GameState, rng: random.Random,
        scene: Scenario = SCENARIOS[0],
    ) -> str:
        if not self.rotates:
            return rng.choice(self.keys)
        if self.mood_aware:
            # 会读人的玩家挑的是"此刻期望收益最高"的那把——把钝化、档位效力
            # 与**当前防备**一起算进去，正是真懂方法论的人在做的事。
            # 防备那一项 8-17 才补：少了它，模型里没有人会在他竖起防备时
            # 改用支持自主或反映式倾听，而那恰恰是这次新增的那半套动作的用途。
            mood = mood_for(state.trust)
            table = scene.efficacy
            guard_factor = max(GUARD_FLOOR, 1 - GUARD_STEP * state.guard)
            return max(
                self.keys,
                key=lambda k: (
                    KEY_VALUES[k]
                    * BLUNT[min(state.used.get(k, 0), len(BLUNT) - 1)]
                    * table[k][mood]
                    * (1.0 if k in GUARD_IMMUNE else guard_factor),
                    -self.keys.index(k),
                ),
            )
        # 挑用得最少的那把；并列时按 KEY_ORDER 取高价值的
        return min(self.keys, key=lambda k: (state.used.get(k, 0), self.keys.index(k)))


# 五种人设。占比合计 100%。
#
# 占比在判分卡撤掉之后重估过：界面每一轮都把「+20 · 苏格拉底式提问 · 扎根」
# 印在屏幕上时，任何会读字的玩家两轮就滑向 expert，人群分布是假的；
# 现在屏幕上只有一个情绪词，人群会停在 novice / average 一侧。
# 大量责骂说教，偶尔命中
#
# breach_rate 是 8-17 补的一条轴，各人设的取值按"他知不知道那条线在哪"给：
# 新手压根不知道投顾不能荐股（0.10，十轮里会踩一次），
# 老手知道但急了仍会滑（0.04 / 0.01），
# 真懂方法论的人不会踩（0.00——这条线是他这份工作的第一课）。
# parrot 给 0.03：攻略里"给他个替代方案"这类话本来就在流传。
#
# warning_rate 是 8-17 补的第二条轴。取值不按"技巧高低"给，按**想不想说**给：
# 「我觉得这是骗局」是所有人都想说的一句话，新手一上来就说（于是退化成空口
# 断言），老手忍着等他晃起来再说。所以新手的值不低于老手——差别在时机，不在频率。
NOVICE = Persona("novice", 0.25, key_rate=0.20, grounded_rate=0.35,
                 penalty_rate=0.63, rotates=False, breach_rate=0.10,
                 warning_rate=0.20, trained=False)
# 混合
AVERAGE = Persona("average", 0.40, key_rate=0.46, grounded_rate=0.47,
                  penalty_rate=0.24, rotates=True, breach_rate=0.04,
                  warning_rate=0.12)
# 高命中、会轮换钥匙，而且看得懂他现在到了哪一档
EXPERT = Persona("expert", 0.20, key_rate=0.78, grounded_rate=0.78,
                 penalty_rate=0.07, rotates=True, mood_aware=True,
                 breach_rate=0.01, warning_rate=0.25)
# 真懂方法论、几乎不失手。
# 不给满分 1.0：判分要过一次模型分类，没有任何玩家能让它每一轮都判成扎根。
# 满分人设是确定性的，胜率只会是 0% 或 100%——那样的模型量不出难度，
# 只会在门槛上给出一个假的绿灯。
SPEEDRUN = Persona("speedrun", 0.03, key_rate=0.97, grounded_rate=0.92,
                   penalty_rate=0.02, rotates=True, mood_aware=True,
                   warning_rate=0.25)
# 照攻略复读固定句子：背哪句说哪句，不会挑用得最少的那把，因此撞钝化。
# 攻略里「直接告诉他这是诈骗」永远排在第一条，所以 warning_rate 给得最高——
# 而它不会读档位，于是这些话大半退化成空口断言。
PARROT = Persona("parrot", 0.12, key_rate=1.0, grounded_rate=0.12,
                 penalty_rate=0.05, rotates=False, breach_rate=0.03,
                 warning_rate=0.40, trained=False)

PERSONAS: Tuple[Persona, ...] = (NOVICE, AVERAGE, EXPERT, SPEEDRUN, PARROT)


@dataclass(frozen=True)
class GameResult:
    ending: Ending
    rounds: int


@dataclass(frozen=True)
class Stats:
    persona: str
    games: int
    win_rate: float
    blacklist_rate: float
    mean_rounds: float
    # 各档结局的占比，键为 Ending.value。胜率只看最高一档，
    # 而结局四档要回答的是另一个问题：有多少人拿到的是一张能发出去的卡。
    ladder: Dict[str, float]


def play_game(
    persona: Persona, rng: random.Random, *, grounding_gate: bool = True,
    scene: Scenario = SCENARIOS[0],
) -> GameResult:
    """跑完一局。evaluate_turn 保证第 12 轮必定出结局，循环不会不终止。"""
    state = new_game()
    while True:
        hits, grounded = persona.act(state, rng, scene)
        # 关掉扎根门控 = 一切命中都按扎根算，用于 §9.2 的对照实验
        outcome = evaluate_turn(
            state, hit_keys=hits, grounded=grounded or not grounding_gate,
            efficacy_table=scene.efficacy,
            mistimed_moods=scene.mistimed_warning_moods,
        )
        state = outcome.state
        if outcome.ending is not None:
            return GameResult(ending=outcome.ending, rounds=state.round)


def simulate(
    persona: Persona, games: int, seed: int, *, grounding_gate: bool = True,
    scene: Scenario = SCENARIOS[0],
) -> Stats:
    # 每个人设一条独立的随机流，加不加人设都不会扰动其他人设的结果。
    # 场景也进种子：两个场景各自可复现，且互不干扰
    rng = random.Random(f"{seed}:{scene.id}:{persona.name}")
    counts: Counter[str] = Counter()
    total_rounds = 0
    for _ in range(games):
        result = play_game(persona, rng, grounding_gate=grounding_gate, scene=scene)
        counts[result.ending.value] += 1
        total_rounds += result.rounds
    return Stats(
        persona=persona.name,
        games=games,
        win_rate=counts[Ending.PERSUADED.value] / games,
        blacklist_rate=counts[Ending.BLACKLISTED.value] / games,
        mean_rounds=total_rounds / games,
        ladder={e.value: counts[e.value] / games for e in Ending},
    )


def run_all(
    games: int, seed: int, *, grounding_gate: bool = True,
    scene: Scenario = SCENARIOS[0],
) -> Dict[str, Stats]:
    return {
        p.name: simulate(p, games, seed, grounding_gate=grounding_gate, scene=scene)
        for p in PERSONAS
    }


def weighted(results: Dict[str, Stats], field: str) -> float:
    return sum(
        getattr(results[p.name], field) * p.share for p in PERSONAS
    )


def weighted_ladder(results: Dict[str, Stats], kind: str) -> float:
    return sum(results[p.name].ladder[kind] * p.share for p in PERSONAS)


def _weighted_choice(
    options: Sequence[Tuple[str, float]], rng: random.Random
) -> str:
    roll = rng.random() * sum(w for _, w in options)
    for name, weight in options:
        roll -= weight
        if roll <= 0:
            return name
    return options[-1][0]


# ── §9.4 验收门槛 ──────────────────────────────────────────────────────────
#
# expert / speedrun 的两条上限是这套门槛的关键：原来一条都没有，
# 于是 98.4% / 5.5 轮那条线一路绿灯走到了线上——会玩的人稳赢，游戏没有难度。
#
# 总体胜率的区间是**推导出来的，不是拍的**。扫了约 500 组参数之后，
# 「expert ≤60%」与「总体 25–35%」被证明互斥——帕累托前沿长这样：
#
#     expert 上限   可达加权总体   speedrun   parrot
#     ≤60%          16.5%          87.5%      2.8%
#     ≤80%          21.9%          96.5%      5.9%
#     ≤90%          28.2%          99.2%     14.7%
#
# 原因是结构性的，不是参数没调好：average 与 expert 每轮产出差 2.5 倍，
# 而"12 轮累加过一条线"是个 S 形判据，会把 2.5 倍放大成十几倍的胜率差。
# 要总体上 25%，expert 必须放回 77–88%——那正是这次重设计要消灭的东西。
# 于是取前沿上保 expert 的那一端，区间跟着实测值走。
WIN_RATE_BAND = (0.12, 0.22)
EXPERT_WIN_CEILING = 0.60
SPEEDRUN_WIN_CEILING = 0.85
PARROT_WIN_CEILING = 0.15
BLACKLIST_CEILING = 0.12

# 结局四档要守的不是难度，是**打完之后手里有没有一张发得出去的卡**。
# 改档之前 87% 的人拿到同一张「他还是转走了」，没有人会把失败截图发到群里，
# 病毒循环在源头就断了。这条盯的是最底下那一档有多挤——劝住线一动不动，
# 所以它与上面几条难度门槛不冲突：调难度不会碰它，把阶梯改窄了才会。
WORST_TIER_CEILING = 0.60


def check_thresholds(results: Dict[str, Stats]) -> List[str]:
    """返回未通过的门槛说明；空列表表示全部通过。"""
    failures = []

    overall = weighted(results, "win_rate")
    if not WIN_RATE_BAND[0] <= overall <= WIN_RATE_BAND[1]:
        failures.append(
            f"加权总体胜率 {overall:.1%} 落在 "
            f"{WIN_RATE_BAND[0]:.0%}–{WIN_RATE_BAND[1]:.0%} 之外"
        )

    expert = results["expert"].win_rate
    if expert > EXPERT_WIN_CEILING:
        failures.append(
            f"expert 胜率 {expert:.1%} > {EXPERT_WIN_CEILING:.0%}"
            "——会读人的玩家稳赢，这局就没有难度可言了"
        )

    speedrun = results["speedrun"].win_rate
    if speedrun > SPEEDRUN_WIN_CEILING:
        failures.append(
            f"speedrun 胜率 {speedrun:.1%} > {SPEEDRUN_WIN_CEILING:.0%}"
            "——完美执行也该有输的时候，否则最优解一被摸清就没得玩了"
        )

    parrot = results["parrot"].win_rate
    if parrot >= PARROT_WIN_CEILING:
        failures.append(
            f"parrot 胜率 {parrot:.1%} ≥ {PARROT_WIN_CEILING:.0%}"
            "——攻略传开当天游戏即报废"
        )

    blacklist = weighted(results, "blacklist_rate")
    if blacklist >= BLACKLIST_CEILING:
        failures.append(
            f"整体被拉黑率 {blacklist:.1%} ≥ {BLACKLIST_CEILING:.0%}"
            "——太多人会在中途被踢出局，投票转化率受损"
        )

    worst = weighted_ladder(results, Ending.TRANSFERRED.value)
    if worst > WORST_TIER_CEILING:
        failures.append(
            f"落到最低一档（转账）的比例 {worst:.1%} > {WORST_TIER_CEILING:.0%}"
            "——大多数人打完只拿到一张发不出去的卡，阶梯就白分了"
        )

    return failures


def format_table(results: Dict[str, Stats]) -> str:
    lines = [
        f"{'人设':<10}{'占比':>8}{'胜率':>9}{'被拉黑':>9}{'平均轮数':>10}",
        "─" * 46,
    ]
    for p in PERSONAS:
        s = results[p.name]
        lines.append(
            f"{p.name:<10}{p.share:>7.0%}{s.win_rate:>9.1%}"
            f"{s.blacklist_rate:>9.1%}{s.mean_rounds:>10.1f}"
        )
    lines.append("─" * 46)
    lines.append(
        f"{'加权总体':<8}{'':>8}{weighted(results, 'win_rate'):>9.1%}"
        f"{weighted(results, 'blacklist_rate'):>9.1%}"
    )
    return "\n".join(lines)


# 阶梯顺序即 Ending 的声明顺序：劝住 / 拦下 / 拖住 / 转账 / 被拉黑
ENDING_LABELS: Dict[str, str] = {
    Ending.PERSUADED.value: "劝住",
    Ending.INTERCEPTED.value: "拦下",
    Ending.STALLED.value: "拖住",
    Ending.TRANSFERRED.value: "转账",
    Ending.BLACKLISTED.value: "被拉黑",
}


def format_ladder(results: Dict[str, Stats]) -> str:
    """结局四档的分布。胜率那张表看的是难度，这张看的是"分享卡上写什么"。"""
    head = "".join(f"{label:>8}" for label in ENDING_LABELS.values())
    lines = [f"{'人设':<10}{head}", "─" * (10 + 8 * len(ENDING_LABELS))]
    for p in PERSONAS:
        row = "".join(
            f"{results[p.name].ladder[kind]:>8.1%}" for kind in ENDING_LABELS
        )
        lines.append(f"{p.name:<10}{row}")
    lines.append("─" * (10 + 8 * len(ENDING_LABELS)))
    lines.append(
        f"{'加权总体':<8}"
        + "".join(f"{weighted_ladder(results, kind):>8.1%}" for kind in ENDING_LABELS)
    )
    return "\n".join(lines)


def format_best_keys() -> str:
    """每个场景、每个情绪档位上**期望收益最高的那一把钥匙**。

    这张表是 POSITIONING 第 5 步的验收判据，也是这个产品最核心那句主张
    唯一一处能被直接验证的地方：**换场景之后最优解必须跟着变**。
    某两列一模一样，就说明后一个场景只是换了套皮，白做。

    场景多于两个之后，"四档里有几档不同"这个总数不够用了：四个场景在
    同一档上给出三种答案也算"不同"，可其中两个仍可能互为换皮。所以下面
    额外按**两两配对**报一次最小差异档数，与 `test_换了场景最优解必须跟着变`
    的判据一致。
    """
    from app.scoring import KEY_VALUES

    moods = (Mood.GUARDED, Mood.IRRITATED, Mood.WAVERING, Mood.SOFTENING)
    labels = {Mood.GUARDED: "戒备", Mood.IRRITATED: "烦躁",
              Mood.WAVERING: "动摇", Mood.SOFTENING: "松动"}
    width = 22
    head = "".join(f"{s.id:>{width}}" for s in SCENARIOS)
    rule = "─" * (8 + width * len(SCENARIOS))
    lines = ["每档最优解（基值 × 档位效力，未钝化）",
             f"{'档位':<8}{head}", rule]

    picks: Dict[str, List[str]] = {s.id: [] for s in SCENARIOS}
    flips = 0
    for mood in moods:
        cells = []
        row = []
        for scene in SCENARIOS:
            table = scene.efficacy
            best = max(KEY_VALUES, key=lambda k: KEY_VALUES[k] * table[k][mood])
            row.append(best)
            picks[scene.id].append(best)
            cells.append(f"{best:>{width}}")
        if len(set(row)) > 1:
            flips += 1
        lines.append(f"{labels[mood]:<7}{''.join(cells)}")
    lines.append(rule)
    lines.append(f"四档里有 {flips} 档的最优解不止一种。")

    pairs = [
        (a, b, sum(x != y for x, y in zip(picks[a], picks[b])))
        for a, b in combinations(picks, 2)
    ]
    worst = min(pairs, key=lambda p: p[2])
    lines.append("两两之间不同的档数：" + "、".join(
        f"{a}/{b} {n}/4" for a, b, n in pairs
    ))
    lines.append(
        f"最接近的一对是 {worst[0]}/{worst[1]}（{worst[2]}/4）。"
        + ("✅ 场景是可迁移的" if worst[2] >= 2
           else "✗ 这两个场景最优解几乎一样——后一个没有存在的必要")
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="判分引擎平衡验证")
    parser.add_argument("--games", type=int, default=20000, help="每个人设的局数")
    parser.add_argument("--seed", type=int, default=SEED, help="随机种子")
    parser.add_argument(
        "--scenario", default="", help="只跑这一个场景（chen / zhou）"
    )
    parser.add_argument(
        "--no-grounding",
        action="store_true",
        help="关闭扎根门控，跑 §9.2 的对照实验",
    )
    args = parser.parse_args()

    gate = not args.no_grounding
    print(f"每人设 {args.games} 局，种子 {args.seed}，"
          f"扎根门控 {'开' if gate else '关'}")

    scenes = [s for s in SCENARIOS if not args.scenario or s.id == args.scenario]
    failed = False
    for scene in scenes:
        results = run_all(args.games, args.seed, grounding_gate=gate, scene=scene)
        print()
        print(f"══ 场景 {scene.id} · {scene.name}（{scene.kind}）"
              + "═" * 20)
        print()
        print(format_table(results))
        print()
        print(format_ladder(results))
        print()
        if not gate:
            print("（对照实验不校验门槛：关掉扎根门控本来就会破门）")
            continue
        failures = check_thresholds(results)
        if failures:
            failed = True
            print(f"§9.4 门槛未通过（{scene.id}）：")
            for f in failures:
                print(f"  ✗ {f}")
        else:
            print(f"§9.4 门槛全部通过（{scene.id}）")

    if gate and len(scenes) > 1:
        print()
        print(format_best_keys())

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
