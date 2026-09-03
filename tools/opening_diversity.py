"""开场白撞车率：量一个 act_eval 量不到的东西。

act_eval 量的是模型**逐轮生成**的台词有多不重样；开场白根本不调模型——
「首屏 ≤3 秒」靠预置台词买单（见 app/persona.py `opening_for`），
是从"人格变体 × 该变体的开场白"这个有限池子里，按 gid 哈希抽一句。

有限池子意味着这件事可以精确算，不用蒙特卡洛：同一个人连开 K 局，
撞见至少一句重复开场白的概率是经典生日问题，池子大小 N = 人格数 × 每人
开场白条数。零模型调用，秒级出数。

用法：
    python -m tools.opening_diversity            # 每个场景各自的撞车率表
    python -m tools.opening_diversity --sessions 15,30

跟 balance_sim 一样：门槛数字未标定时不设，只把数字摆出来——
一个凭空定的门槛比没有门槛更坏。这条门槛该设多少，是产品判断，不是这个
脚本能替你拍板的事。
"""

from __future__ import annotations

import argparse
from math import factorial
from typing import Tuple

from app.persona import Persona
from app.scenario import SCENARIOS as _SCENES

# **从 app.scenario 派生，不在这儿抄一份清单。**
# 原来这里写死着四个二元组，加第五个场景时它不会报任何错——
# 只会安静地少算一个，而这个脚本的产出正是"开场白够不够用"。
# 这与 act_eval 那两处漏传 scene 是同一类病（8-22 一起收的）：
# **一份手抄的清单，就是一个迟早会漏的地方。**
SCENARIOS: Tuple[Tuple[str, Tuple[Persona, ...]], ...] = tuple(
    (f"{s.id}（{s.kind.split(' · ')[0]}）", s.personas) for s in _SCENES
)

DEFAULT_SESSIONS: Tuple[int, ...] = (5, 10, 15, 20, 30)


def pool_size(personas: Tuple[Persona, ...]) -> int:
    """开场白池子大小。假定每个人格的开场白条数一致（骨架冻结的一部分）；
    不一致时用实际总数——池子大小才是撞车率唯一关心的数，谁贡献了几条不重要。
    """
    return sum(len(p.openings) for p in personas)


def collision_probability(pool: int, sessions: int) -> float:
    """K 局里至少撞见一次重复开场白的概率（经典生日问题）。

    P(全不重复) = pool! / ((pool-K)! × pool^K)，K > pool 时必然重复（=1.0）。
    直接算阶乘比累乘概率更精确，池子小、局数也小，不会有精度问题。
    """
    if sessions > pool:
        return 1.0
    if sessions <= 1:
        return 0.0
    no_collision = factorial(pool) / (factorial(pool - sessions) * pool ** sessions)
    return 1 - no_collision


def format_report(sessions: Tuple[int, ...] = DEFAULT_SESSIONS) -> str:
    lines = ["开场白池子大小，以及连开 K 局撞见重复开场白的概率", ""]
    head = f"{'场景':<12}{'人格数':>6}{'池子':>6}" + "".join(f"{k:>8}局" for k in sessions)
    lines.append(head)
    lines.append("─" * len(head))
    for name, personas in SCENARIOS:
        pool = pool_size(personas)
        row = f"{name:<12}{len(personas):>6}{pool:>6}"
        row += "".join(f"{collision_probability(pool, k):>8.1%}" for k in sessions)
        lines.append(row)
    lines.append("")
    lines.append(
        "开场白不调模型（首屏 ≤3 秒），是从这个固定池子里按 gid 哈希抽的——"
        "池子越小，测得越勤，越容易撞见一模一样的第一句话。"
        "act_eval 量的是模型逐轮生成的台词，这张表量的是它量不到的那一段。"
    )
    lines.append(
        "表里的 K 局，指的是落到**这一个**场景的局数，不是玩家总共打了几局——"
        f"场景从 2 个增加到 {len(SCENARIOS)} 个之后（`scenario_for_trigger` 按异动类型分配），"
        f"打 K 局总数，落到某一个场景平均只有 K/{len(SCENARIOS)} 局，实际撞车率"
        "比表面数字更低。这也是当初把新场景至少设两个、而不是一个的理由之一。"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="开场白撞车率")
    parser.add_argument(
        "--sessions", default="", help="逗号分隔的局数列表，默认 5,10,15,20,30"
    )
    args = parser.parse_args()
    sessions = (
        tuple(int(x) for x in args.sessions.split(",")) if args.sessions
        else DEFAULT_SESSIONS
    )
    print(format_report(sessions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
