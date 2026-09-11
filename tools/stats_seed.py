"""给 Redis 灌一批**造出来的**对局，以及把它们清掉。

## 这个脚本存在的理由

复盘里「别人打成什么样」那一节要有样本才出现（信任度百分位的门槛见
`PERCENTILE_FLOOR`，**按场景各算各的**）。2026-08-18 为了验证渲染与分桶，
手写 Redis 命令造过一批数据。
那次留下了三个毛病，这个脚本是来收拾它们的：

1. **只写了 trust 一个键。** 于是本机 Redis 现在是
   `stats:games=116`、`turns`/`endings`/`hits` 三个键根本不存在——
   一个自相矛盾的状态。`/api/stats` 照实吐出来，前端靠 `if (!data.turns)`
   把整节藏了，所以一直没人发现。**手写 Redis 命令必然漏键**，
   计数器只有一起动才是自洽的，所以这里一律走 `app.stats.Stats`
   （键名、db0 钉死、命名空间前缀全部取自生产代码，不在这儿抄一份）。
2. **只有老陈。** 8-21 信任度分布改成按场景分桶之后，一个场景的样本
   救不了另外三个——本机 `trust:chen` 只有 1 局，`trust:zhou` 有 24 局，
   liu / ben 一局都没有。
3. **不可复现，也说不清是怎么来的。** 现在给定 `--seed` 就是同一批。

## 这批数据是什么、不是什么

**是**：走生产同一套判分引擎（`evaluate_turn`）、同一套人设权重
（`balance_sim` 的 NOVICE/AVERAGE/EXPERT/SPEEDRUN/PARROT 及其占比）
算出来的结局、轮数、信任度与命中分布。它验的是**渲染与分桶**。

**不是**：真实对局。它跳过了模型演绎，没有一句台词，也没有任何真人
在读那些台词之后做出的选择。**任何"玩家实际上会怎么打"的结论都不能
从这批数据里读**——那种结论只能来自真人跑的局。

因此：灌进去之前先想清楚这台 Redis 上的数会被谁看见。

用法：
    python -m tools.stats_seed --dry-run          # 只打印会写什么，不连 Redis
    python -m tools.stats_seed                    # 每个场景凑够百分位门槛
    python -m tools.stats_seed --games 50 --scenario zhou
    python -m tools.stats_seed --purge            # 清掉本作品的全部统计键
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple
from urllib.parse import urlsplit

from app.scenario import SCENARIOS, Scenario
from app.scoring import Ending, GameState, evaluate_turn, new_game
from tools.balance_sim import PERSONAS, Persona

# 复盘里百分位那一节的门槛（`static/stats.js` 的 `TRUST_SAMPLE_MIN`）。
# 少于这个数就不显示——宁可不显示，也不显示一个不成立的排名。
#
# **2026-09-11 跟着前端从 20 降到 8。** 降的理由写在 stats.js 那个常量上面
# （20 乘上六个场景 = 一百二十局入档对局，那条线真实场合里跨不过去），
# 换来的条件是那句话现在自带分母。这里是它的第二份，两处必须一起改——
# 前端那份是真正生效的，这份只决定脚本灌多少。
PERCENTILE_FLOOR = 8

# 8-21 之前信任度分布是一个全局哈希，键名里不带场景。改成按场景分桶之后
# 那个键**再也没有人读**，而大赛的 Redis 是共享 db0——留一把没人读的键在
# 别人的库里，是这次要顺手收掉的东西。`--purge` 会把它一并删掉。
LEGACY_TRUST_KEY = "ai-antifraud-persuasion:stats:trust"


@dataclass(frozen=True)
class SeedGame:
    """一局造出来的对局，四个计数器各取所需。"""

    ending: Ending
    rounds: int
    trust: int
    hits_per_turn: Tuple[Tuple[str, ...], ...]


def play(persona: Persona, rng: random.Random, scene: Scenario) -> SeedGame:
    """跑完一局，把四个计数器要的东西都留下来。

    与 `balance_sim.play_game` 是同一个循环，差别只在收集什么：那边只要
    结局与轮数（它算的是胜率），这边还要最终信任度与逐轮命中——
    **不复用它是因为它的返回值里没有这两样**，而多返回两个字段会让
    蒙特卡洛那边每局多背两个对象，两万局 × 五人设 × 四场景是白花的钱。
    """
    state: GameState = new_game()
    hits_per_turn: List[Tuple[str, ...]] = []
    while True:
        hits, grounded = persona.act(state, rng, scene)
        hits_per_turn.append(tuple(hits))
        outcome = evaluate_turn(
            state,
            hit_keys=hits,
            grounded=grounded,
            efficacy_table=scene.efficacy,
            mistimed_moods=scene.mistimed_warning_moods,
        )
        state = outcome.state
        if outcome.ending is not None:
            return SeedGame(
                ending=outcome.ending,
                rounds=state.round,
                trust=state.trust,
                hits_per_turn=tuple(hits_per_turn),
            )


def pick(rng: random.Random) -> Persona:
    """按投票日人群占比抽一个玩家类型。

    不等概率抽：真实人群里 novice 占 25%、speedrun 占 3%，均匀抽会造出
    一批"平均水平高得离谱"的假样本，而百分位正是拿这批人当分母的。
    """
    roll = rng.random() * sum(p.share for p in PERSONAS)
    for persona in PERSONAS:
        roll -= persona.share
        if roll <= 0:
            return persona
    return PERSONAS[-1]


# 一局一局往上加时的上限。加权被拉黑率实测 6.5%~10.6%，凑不够门槛那点入档
# 局数的可能性微乎其微，但一个没有上限的 while 循环不该出现在任何脚本里。
_MAX_GAMES = 200


def seed_games(scene: Scenario, seed: int, games: int = 0) -> List[SeedGame]:
    """造一批局。`games` 为 0 时**按入档局数**凑够百分位门槛。

    这个区别是 `--dry-run` 当场量出来的，值得写下来：**灌 N 局不等于
    有 N 个样本。** 被拉黑不入档（`app/stats.py::_LADDER_KINDS`：提前出局，
    信任度必然是 0，混进分布会把所有人的百分位顶得虚高），而加权被拉黑率
    有 6.5%~10.6%——当年门槛还是 20 时，灌 20 局造出来的是 17~19 个样本，
    **正好卡在门槛下面，百分位那一行一行都不会出现**。8-18 那次八成就栽在
    这儿；门槛降到 8 之后差额小了，但"灌几局"和"有几个样本"仍然是两个数。

    所以这里数的是入档局数，不是灌了几局。
    """
    rng = random.Random(f"seed:{seed}:{scene.id}")
    if games:
        return [play(pick(rng), rng, scene) for _ in range(games)]

    batch: List[SeedGame] = []
    ladder = 0
    while ladder < PERCENTILE_FLOOR and len(batch) < _MAX_GAMES:
        game = play(pick(rng), rng, scene)
        batch.append(game)
        ladder += game.ending is not Ending.BLACKLISTED
    return batch


def summarize(batch: Sequence[SeedGame]) -> Dict[str, object]:
    """打印用。`--dry-run` 靠它在不连 Redis 的情况下说清会写进去什么。"""
    endings: Counter = Counter(g.ending.value for g in batch)
    hits: Counter = Counter(h for g in batch for turn in g.hits_per_turn for h in turn)
    ladder = sum(
        1 for g in batch if g.ending is not Ending.BLACKLISTED
    )
    return {
        "games": len(batch),
        "turns": sum(g.rounds for g in batch),
        "endings": dict(endings),
        "hits": dict(hits.most_common()),
        # 进信任度分布的只有走到阶梯里的四档（app/stats.py `_LADDER_KINDS`）。
        # 被拉黑不入档，混进来会把所有人的百分位顶得虚高
        "ladder_samples": ladder,
    }


# ── 写入 ───────────────────────────────────────────────────────────────────


def _is_local(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "")


async def write(url: str, scenes: Sequence[Tuple[Scenario, List[SeedGame]]]) -> None:
    """一律经 `app.stats.Stats` 写。

    **不在这儿拼键名。** 8-18 那次就是手写命令，结果只写了 trust 一个键，
    命名空间前缀、db0 钉死、分桶算法各存了一份在人的脑子里。
    这里连 `_trust_bucket` 都不碰：`record_trust` 收的是原始信任度。
    """
    from app.stats import Stats

    store = Stats(url)
    if not store.enabled:
        raise SystemExit("Redis 不可用：REDIS_URL 没配，或者没装 redis 包")

    for scene, batch in scenes:
        for game in batch:
            store.record_start()
            for turn in game.hits_per_turn:
                store.record_turn(turn)
            store.record_ending(game.ending.value)
            store.record_trust(game.ending.value, game.trust, scene.id)

    # 写入是 fire-and-forget 的（对局路径上不能等 Redis），所以这里必须
    # 等挂起的任务收干净再退出，否则进程一结束就把它们全取消了
    from app.stats import _pending

    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def purge(url: str) -> List[str]:
    """删掉本作品的全部统计键，含那把没人读的旧全局键。

    只删自己命名空间下的——共享 db0 上，一个 `FLUSHDB` 会把几十个作品
    一起抹掉。这里连 `--scan` 都限定前缀。
    """
    from app.stats import KEY_ENDINGS, KEY_GAMES, KEY_HITS, KEY_TURNS, Stats, key_trust

    store = Stats(url)
    if not store.enabled:
        raise SystemExit("Redis 不可用：REDIS_URL 没配，或者没装 redis 包")

    keys = [
        KEY_GAMES, KEY_TURNS, KEY_ENDINGS, KEY_HITS,
        LEGACY_TRUST_KEY,
        *(key_trust(s.id) for s in SCENARIOS),
        key_trust(""),
    ]
    conn = store._conn()  # noqa: SLF001 - 维护脚本，刻意走同一个连接配置
    existing = [k for k, n in zip(keys, await asyncio.gather(
        *(conn.exists(k) for k in keys)
    )) if n]
    if existing:
        await conn.delete(*existing)
    return existing


def main() -> int:
    parser = argparse.ArgumentParser(description="给 Redis 灌造出来的对局 / 清掉它们")
    parser.add_argument(
        "--scenario", default="", choices=["", *(s.id for s in SCENARIOS)],
        help="只灌这一个场景，默认四个都灌",
    )
    parser.add_argument(
        "--games", type=int, default=0,
        help=f"每个场景固定灌几局；默认按**入档**局数凑够 {PERCENTILE_FLOOR}"
             "（被拉黑不入档，灌几局和有几个样本是两个数）",
    )
    parser.add_argument("--seed", type=int, default=818, help="给定就可复现")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不连 Redis")
    parser.add_argument("--purge", action="store_true", help="清掉本作品的统计键")
    parser.add_argument(
        "--yes", action="store_true",
        help="连的不是本机 Redis 时必须显式给出——大赛那台是共享实例",
    )
    args = parser.parse_args()

    scenes = [s for s in SCENARIOS if not args.scenario or s.id == args.scenario]
    url = os.environ.get("REDIS_URL", "")
    if not url:
        from app.config import settings
        url = settings.redis_url

    if not args.dry_run and not _is_local(url) and not args.yes:
        print(
            f"REDIS_URL 指向的不是本机（{urlsplit(url).hostname}）。\n"
            "大赛那台是**共享实例**，几十个作品挤在同一个 db0 里，"
            "而这批数据是造出来的、不是真实对局。\n"
            "确认要写就加 --yes。",
            file=sys.stderr,
        )
        return 2

    if args.purge:
        if args.dry_run:
            print("--dry-run 与 --purge 一起用没有意义：清掉什么取决于库里有什么")
            return 2
        deleted = asyncio.run(purge(url))
        print(f"删掉 {len(deleted)} 个键：" + ("、".join(deleted) or "（本来就没有）"))
        return 0

    batches = [(s, seed_games(s, args.seed, args.games)) for s in scenes]

    for scene, batch in batches:
        info = summarize(batch)
        print(f"\n{scene.id}（{scene.name}）")
        print(f"  {info['games']} 局 / {info['turns']} 轮，"
              f"入档 {info['ladder_samples']} 局"
              + ("" if int(info["ladder_samples"]) >= PERCENTILE_FLOOR
                 else f"  ← 不足 {PERCENTILE_FLOOR}，百分位那一行不会出现"))
        print(f"  结局 {info['endings']}")
        print(f"  命中 {info['hits']}")

    if args.dry_run:
        print("\n--dry-run：一个字都没写进 Redis")
        return 0

    asyncio.run(write(url, batches))
    print(
        f"\n已写入 {urlsplit(url).hostname or '本机'}。"
        "\n**这批是造出来的，不是真实对局**：走的是同一套判分引擎，"
        "但跳过了模型演绎，没有一句台词。"
        "\n它验的是渲染与分桶，不能拿来说'玩家实际上会怎么打'。"
        "\n清掉：python -m tools.stats_seed --purge"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
