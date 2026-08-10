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
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from app.scoring import Ending, GameState, evaluate_turn, new_game

# 轮换时的偏好顺序：先高价值，同已用次数时按此序取
# 固定种子：蒙特卡洛入 CI 的前提是它不能偶尔变红
SEED = 20260903

KEY_ORDER: Tuple[str, ...] = (
    "anchor_real_purpose",
    "socratic_question",
    "expose_contradiction",
)

# 失误的相对频率。novice 的责骂多于说教，说教多于空口断言。
PENALTY_WEIGHTS: Tuple[Tuple[str, float], ...] = (
    ("scold", 0.40),
    ("preach", 0.35),
    ("bare_assertion", 0.25),
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
    penalty_rate: float   # 单轮触发失误的概率
    rotates: bool         # 是否会轮换钥匙（懂行的人不会复读一招）

    def act(self, state: GameState, rng: random.Random) -> Tuple[List[str], bool]:
        hits: List[str] = []
        grounded = False

        if rng.random() < self.key_rate:
            hits.append(self._pick_key(state, rng))
            grounded = rng.random() < self.grounded_rate

        if rng.random() < self.penalty_rate:
            hits.append(_weighted_choice(PENALTY_WEIGHTS, rng))

        return hits, grounded

    def _pick_key(self, state: GameState, rng: random.Random) -> str:
        if not self.rotates:
            return rng.choice(KEY_ORDER)
        # 挑用得最少的那把；并列时按 KEY_ORDER 取高价值的
        return min(KEY_ORDER, key=lambda k: (state.used.get(k, 0), KEY_ORDER.index(k)))


# §9.1 的五种人设。占比合计 100%。
# 大量责骂说教，偶尔命中
NOVICE = Persona("novice", 0.20, key_rate=0.20, grounded_rate=0.35,
                 penalty_rate=0.63, rotates=False)
# 混合
AVERAGE = Persona("average", 0.40, key_rate=0.46, grounded_rate=0.47,
                  penalty_rate=0.24, rotates=True)
# 高命中、会轮换钥匙
EXPERT = Persona("expert", 0.22, key_rate=0.78, grounded_rate=0.78,
                 penalty_rate=0.07, rotates=True)
# 真懂方法论、完美执行
SPEEDRUN = Persona("speedrun", 0.05, key_rate=1.0, grounded_rate=1.0,
                   penalty_rate=0.0, rotates=True)
# 照攻略复读固定句子：背哪句说哪句，不会挑用得最少的那把，因此撞钝化
PARROT = Persona("parrot", 0.13, key_rate=1.0, grounded_rate=0.12,
                 penalty_rate=0.05, rotates=False)

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


def play_game(
    persona: Persona, rng: random.Random, *, grounding_gate: bool = True
) -> GameResult:
    """跑完一局。evaluate_turn 保证第 12 轮必定出结局，循环不会不终止。"""
    state = new_game()
    while True:
        hits, grounded = persona.act(state, rng)
        # 关掉扎根门控 = 一切命中都按扎根算，用于 §9.2 的对照实验
        outcome = evaluate_turn(
            state, hit_keys=hits, grounded=grounded or not grounding_gate
        )
        state = outcome.state
        if outcome.ending is not None:
            return GameResult(ending=outcome.ending, rounds=state.round)


def simulate(
    persona: Persona, games: int, seed: int, *, grounding_gate: bool = True
) -> Stats:
    # 每个人设一条独立的随机流，加不加人设都不会扰动其他人设的结果
    rng = random.Random(f"{seed}:{persona.name}")
    wins = blacklisted = total_rounds = 0
    for _ in range(games):
        result = play_game(persona, rng, grounding_gate=grounding_gate)
        wins += result.ending is Ending.PERSUADED
        blacklisted += result.ending is Ending.BLACKLISTED
        total_rounds += result.rounds
    return Stats(
        persona=persona.name,
        games=games,
        win_rate=wins / games,
        blacklist_rate=blacklisted / games,
        mean_rounds=total_rounds / games,
    )


def run_all(
    games: int, seed: int, *, grounding_gate: bool = True
) -> Dict[str, Stats]:
    return {
        p.name: simulate(p, games, seed, grounding_gate=grounding_gate)
        for p in PERSONAS
    }


def weighted(results: Dict[str, Stats], field: str) -> float:
    return sum(
        getattr(results[p.name], field) * p.share for p in PERSONAS
    )


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
WIN_RATE_BAND = (0.30, 0.40)
PARROT_WIN_CEILING = 0.20
BLACKLIST_CEILING = 0.10


def check_thresholds(results: Dict[str, Stats]) -> List[str]:
    """返回未通过的门槛说明；空列表表示全部通过。"""
    failures = []

    overall = weighted(results, "win_rate")
    if not WIN_RATE_BAND[0] <= overall <= WIN_RATE_BAND[1]:
        failures.append(
            f"加权总体胜率 {overall:.1%} 落在 "
            f"{WIN_RATE_BAND[0]:.0%}–{WIN_RATE_BAND[1]:.0%} 之外"
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


def main() -> int:
    parser = argparse.ArgumentParser(description="判分引擎平衡验证")
    parser.add_argument("--games", type=int, default=20000, help="每个人设的局数")
    parser.add_argument("--seed", type=int, default=SEED, help="随机种子")
    parser.add_argument(
        "--no-grounding",
        action="store_true",
        help="关闭扎根门控，跑 §9.2 的对照实验",
    )
    args = parser.parse_args()

    gate = not args.no_grounding
    results = run_all(args.games, args.seed, grounding_gate=gate)

    print(f"每人设 {args.games} 局，种子 {args.seed}，"
          f"扎根门控 {'开' if gate else '关'}")
    print()
    print(format_table(results))
    print()

    if not gate:
        print("（对照实验不校验门槛：关掉扎根门控本来就会破门）")
        return 0

    failures = check_thresholds(results)
    if failures:
        print("§9.4 门槛未通过：")
        for f in failures:
            print(f"  ✗ {f}")
        return 1

    print("§9.4 门槛全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
