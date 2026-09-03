"""线索覆盖率：量一个 act_eval 量不到的失败。

act_eval 量的是老陈**说得像不像人**——不重样、没有 AI 味、说话形状对。
它有一整类失败一条都抓不到：**老陈演得很流畅，但一整局什么都没漏。**

这一局的玩法是信息差（CONTEXT.md「对局」）：开局投顾只知道账户转出过一笔钱，
启航财经、王老师、那只票、三十万、下午三点全得从他嘴里挖出来。挖不出来的
时候有三种原因：

    玩家不会问              → 这是玩法，正是要练的东西
    问了，但不是时候        → 也是玩法：档位指示写着松动档他才主动透露
    问对了时候他还是不说    → 这才是缺陷，而且是静默的

## 这个脚本**分不开**后两种，别拿它当缺陷探测器用

首版的文档写着它能把"玩家不会问"和"老陈没长嘴"分开。**那句话是错的，
第一次跑批就被自己的数据推翻了。**

拿 chen 的 climb 路线量（20 局）：「小雨」那条 8 局里出现，**首现档位 100%
落在松动**——而 chen 的档位指示写的正是「松动：你在犹豫，会主动透露一些
原本不想说的事」。机制严丝合缝。同一条路线上「国家反诈中心」是 0/20，
而翻开路线就看得见原因：**climb 十二轮里没有一句话碰过反诈、警察、家里人
劝过这个话题**，他从头到尾没有开口的机会。0% 在这里不是缺陷，是没人问。

固定路线是**四种玩家**，不是四个等价探针：它们各自只在各自的档位上问各自
那几件事。一条线索没露出来，可能是他不肯说，也可能是这条路线从没在他肯说
的那个档位上问过它——**光看覆盖率这两件事长得一模一样**。

所以下面多印两列，让读的人自己分：

    路线问过没有   路线的固定发言里有没有一句命中这条线索的正则
    首现档位       他第一次抖出来时，那一轮声明的是哪个档位

判据是「首现档位落在松动/动摇 → 机制在按设定走」；「路线问过、他一直不说」
才值得去翻人设与提示词；「路线压根没问过」是路线的空白，与演绎无关。

**已知空白，别去修**：「反诈中心 / 退休教师群」那条（也就是"外部已经劝过他"）
在三个场景的教科书路线上一律「没问」，因此命中率接近 0。
2026-09-03 所有者拍板不补——补它要改路线，而改路线就要五个场景全量重跑
（按 RPM 30 是四五个小时），换来的只是一列更好看的覆盖率。
理由与边界写在 `tests/data/act_routes.jsonl` 的头注里。
**看到那一行的 0% 时，旁边那列的「没问」就是全部解释。**

## 判据从哪来

`app.scenario.PhoneRow.test` 是复盘揭晓清单已经在用的那套正则，跑在
**劝阻对象的台词**上（「判据是他说没说过」，见那个 dataclass 的文档字符串）。
这里一个字都不重写：同一套正则，换一个问法——
复盘问的是"这一局挖出来了吗"，这里问的是"跨 80 局挖得出来吗"。

`test` 为空的那一条（`own=True`，开局就在投顾手里的银行短信）不计入分母，
理由同复盘：它本来就不用挖。五个场景目前都是 4 条可挖 + 1 条已有。

## 外部基准

ROLESafe（CHI 2026，n=144，docs/EVIDENCE.md §五）的 Table 6 量的是同一件事：
LLM 扮演的一方有没有把预设线索真的抖出来。Helper 组 90% 的对话覆盖了全部
三条线索，Experiencer 组只有 34.85%。**分母不同（他们 3 条、我们 4 条），
数字不可直接对比**，但那篇给出了一个事实——这个指标真的会掉下来，而且掉的
时候另外那些指标看不出来。

## 必须按路线拆开看

四条固定路线**不是四个等价的样本**，它们是四种玩家：`climb` 是教科书打法、
`sawtooth` 是认真但没受训的、`cold` 一上来就扣帽子、`parrot` 照攻略复读同一句。
后两条挖不出线索**本来就是对的**——信息差就是玩法，打得差就该挖不出来。

所以合并四条路线算出来的覆盖率没有意义，它量的是"四种玩家的平均"，
而没有任何一个真人是那个平均。**要跟外部基准对齐的是 `climb` 那一列**：
ROLESafe 的被试是被鼓励去劝的、会真的发问的人，对应这里的教科书路线。

## 顺带量泄漏（--leak）

判据"老陈说了 = 玩家挖出来了"在因果上成立的前提是：**老陈只有被问才吐。**
这个前提没被验证过。

判法：某条线索在第 R 轮首次出现在老陈嘴里，而玩家前 R 轮的固定发言没有一句
匹配这条线索的正则，就记一次**未经发问的抖落**。（玩家在第 R 轮先说、老陈在
第 R 轮才回，所以第 R 轮玩家那句算在"已问"里。）

**这个代理会系统性高估泄漏，必须知道它错在哪。** 判据拿线索自己的正则去匹配
玩家的话，而那套正则是照老陈的用词写的。玩家问"那笔钱本来是做什么用的"、
老陈答"我老伴还不知道"——`老伴` 的正则匹配不上玩家那句，于是记成"未经发问"，
而它恰恰是这一局最该发生的事：**一句问对了的话，把线索问了出来。**

真正干净的一列是 `parrot`：它 12 轮只问同一件事（这笔钱的用途），从没提过
王老师、女儿、反诈中心。**在 parrot 那一列里抖出用途以外的线索，才是真白送。**
所以下面的泄漏也按路线拆开印，读的时候只读 parrot 那一列，其余三列是上界。

用法：
    python -m tools.cue_coverage                       # 全部已有语料
    python -m tools.cue_coverage --scenario chen       # 只看一个场景
    python -m tools.cue_coverage --leak                # 附带未经发问的抖落率
    python -m tools.cue_coverage --gate 0.85           # 设了才判 PASS/FAIL

跟 balance_sim、opening_diversity 一样：**门槛不设默认值。**
一个凭空定的门槛比没有门槛更坏——这条该设多少是产品判断，不是脚本能拍板的。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from collections import Counter
from typing import Dict, List, Sequence, Tuple

from app.scenario import SCENARIOS as _SCENES

ROOT = Path(__file__).resolve().parent.parent
ROUTES_PATH = ROOT / "tests" / "data" / "act_routes.jsonl"

# 语料文件名约定，与 docs/HANDOFF.md 里那几条重跑命令一致。
# chen 另有一份没有 sid 的历史文件（baseline-lines.jsonl），**刻意不收**：
# 它是 8-17 的产物，那之后提示词改过，混进来会把今天的基线算歪。
CORPUS = "baseline-{sid}-lines.jsonl"


def _load_jsonl(path: Path) -> List[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        rows.append(json.loads(s))
    return rows


def routes_for(sid: str) -> Dict[str, List[str]]:
    """某场景四条固定路线的玩家发言，按路线 id 收。"""
    out: Dict[str, List[str]] = {}
    for r in _load_jsonl(ROUTES_PATH):
        if r.get("sid") == sid:
            out[r["id"]] = list(r["utterances"])
    return out


def moods_for(sid: str) -> Dict[str, List[str]]:
    """路线逐轮**声明**的档位。

    声明的，不是判出来的——跑批把档位钉死，20 遍之间唯一在变的只有模型自己
    （见 tests/data/act_routes.jsonl 顶部）。所以"他第几轮说的"可以直接翻成
    "他在哪个档位上说的"，不用再判一次。
    """
    out: Dict[str, List[str]] = {}
    for r in _load_jsonl(ROUTES_PATH):
        if r.get("sid") == sid:
            out[r["id"]] = list(r["moods"])
    return out


def conversations(
    rows: Sequence[dict], *, full_only: bool = True,
) -> Tuple[Dict[Tuple[str, int], List[str]], int]:
    """把逐轮的行折回一局一局，并**默认剔掉缺轮的局**。

    返回 (完整的局, 被剔掉的局数)。

    ## 为什么必须剔

    跑批会掉轮次：网关限流、瞬时 5xx，`act_eval` 重试完还不行就记一次失败、
    接着往下跑（那对它自己的指标是对的——空台词会被当成"和另一条空的一样"，
    凭空抬高重复率，比丢掉更糟）。

    但对**这个**脚本，掉轮不是中性的：少一轮就少一次抖出线索的机会，
    覆盖率只会被压低、不会被抬高。**一个 2% 的掉轮率会变成一个方向确定的
    向下偏差**，而这个脚本量的正是覆盖率本身。

    掉几局比把偏差混进数字里好——所以剔掉，并且**把剔掉的数目印出来**：
    剔太多本身就是一条要看见的信号（那说明这批语料该重跑，不是该将就）。
    """
    bucket: Dict[Tuple[str, int], Dict[int, str]] = {}
    for r in rows:
        bucket.setdefault((r["route"], r["run"]), {})[r["round"]] = r["raw"]
    if not bucket:
        return {}, 0
    # 满轮数从这批语料自己推，不写死 12：`--rounds` 跑出来的语料轮数更少，
    # 写死会把整批都判成缺轮
    full = max(max(v) for v in bucket.values())
    kept = {
        k: [v[i] for i in sorted(v)]
        for k, v in bucket.items()
        if not full_only or len(v) == full
    }
    return kept, len(bucket) - len(kept)


def diggable(scene) -> Tuple[Tuple[str, re.Pattern], ...]:
    """可挖线索：(名字, 编好的正则)。`test` 为空的不计入分母。"""
    return tuple(
        (row.name, re.compile(row.test))
        for row in scene.phone
        if row.test
    )


def cover(lines: Sequence[str], clues) -> List[bool]:
    """这一局各条线索抖出来没有。"""
    whole = "\n".join(lines)
    return [bool(rx.search(whole)) for _, rx in clues]


def first_round(lines: Sequence[str], rx: re.Pattern) -> int:
    """老陈第一次抖出这条线索的轮次（1 起）。没抖出来返回 0。"""
    for i, line in enumerate(lines, 1):
        if rx.search(line):
            return i
    return 0


def asked_by(utterances: Sequence[str], rx: re.Pattern, upto: int) -> bool:
    """玩家在前 `upto` 轮里问到过这条线索没有。"""
    return any(rx.search(u) for u in utterances[:upto])


def report(sid: str, leak: bool, gate: float | None) -> bool | None:
    """返回 PASS/FAIL；没设门槛时返回 None。"""
    scene = next(s for s in _SCENES if s.id == sid)
    path = ROOT / CORPUS.format(sid=sid)
    if not path.exists():
        print(f"\n场景 {sid}：**没有语料**，跳过。")
        print(f"  生成：python -m tools.act_eval --scenario {sid} "
              f"--concurrency 8 --dump {path.name}")
        return None

    clues = diggable(scene)
    convs, dropped = conversations(_load_jsonl(path))
    n = len(convs)
    total = len(clues)
    if not n:
        print(f"\n场景 {sid}：{dropped} 局全部缺轮，这批语料不可用。")
        return None
    # 路线按"玩家有多会问"从强到弱排，读表时最左边那一列就是要跟外部基准比的那一列。
    order = [r for r in ("climb", "sawtooth", "cold", "parrot")
             if any(rt == r for rt, _ in convs)]

    print(f"\n场景 {sid}（{scene.kind.split(' · ')[0]}）· {n} 局 × {total} 条可挖线索"
          + (f"　**剔掉 {dropped} 局缺轮的**（掉轮只会把覆盖率压低，见 `conversations`）"
             if dropped else ""))

    def slice_of(route: str) -> List[List[str]]:
        return [ls for (rt, _), ls in convs.items() if rt == route]

    head = "".join(f"{r:>10s}" for r in order)
    print(f"  {'':22s}{head}{'  合计':>8s}")

    # 每局覆盖几条 → 分布，按路线拆开。分母是这个场景自己的可挖条数，不跨场景合并。
    per_route = {r: [sum(cover(ls, clues)) for ls in slice_of(r)] for r in order}
    allhits = [h for r in order for h in per_route[r]]
    for k in range(total, 0, -1):
        cells = "".join(
            f"{sum(1 for h in per_route[r] if h >= k) / len(per_route[r]):>10.1%}"
            for r in order)
        mark = "全覆盖" if k == total else f"≥{k} 条"
        print(f"  {mark:22s}{cells}{sum(1 for h in allhits if h >= k) / n:>8.1%}")
    cells = "".join(f"{sum(per_route[r]) / len(per_route[r]):>10.2f}" for r in order)
    print(f"  {'平均覆盖 / ' + str(total) + ' 条':22s}{cells}{sum(allhits) / n:>8.2f}")

    # 逐条看，找那条最容易掉的。
    print("  逐条命中率：")
    for i, (name, _rx) in enumerate(clues):
        cells = "".join(
            f"{sum(1 for ls in slice_of(r) if cover(ls, clues)[i]) / len(slice_of(r)):>10.1%}"
            for r in order)
        share = sum(1 for ls in convs.values() if cover(ls, clues)[i]) / n
        print(f"    {name[:18]:20s}{cells}{share:>8.1%}")

    # **一个 0% 有两种读法，这两列是用来分开它们的**（见模块文档）。
    says = routes_for(sid)
    moods = moods_for(sid)
    print("  路线问过没有 / 他首次开口时的声明档位：")
    for name, rx in clues:
        cells = []
        for r in order:
            问过 = any(rx.search(u) for u in says.get(r, []))
            档 = []
            for (rt, _run), lines in convs.items():
                if rt != r:
                    continue
                at = first_round(lines, rx)
                if at and at <= len(moods.get(r, [])):
                    档.append(moods[r][at - 1])
            主 = Counter(档).most_common(1)
            cells.append(
                f"{('问过' if 问过 else '没问'):>4s}{(主[0][0][:4] if 主 else '—'):>6s}")
        print(f"    {name[:18]:20s}{''.join(cells)}")

    verdict: bool | None = None
    if gate is not None:
        # **门槛只判 climb**，理由见模块文档：合并四条路线量的是"四种玩家的平均"，
        # 而这个门槛要拦的是"老陈没长嘴"——那只有在玩家真的会问的时候才看得出来。
        best = per_route.get("climb")
        if best:
            full = sum(1 for h in best if h == total) / len(best)
            verdict = full >= gate
            print(f"  {'PASS' if verdict else 'FAIL'} climb 全覆盖率 {full:.1%} "
                  f"{'≥' if verdict else '<'} 门槛 {gate:.0%}")

    if leak:
        routes = routes_for(sid)
        print("  未经发问的抖落（**只读 parrot 那一列**，其余是上界，见模块文档）：")
        for i, (name, rx) in enumerate(clues):
            cells = []
            for r in order:
                tot = un = 0
                says = routes.get(r, [])
                for (rt, _run), lines in convs.items():
                    if rt != r:
                        continue
                    at = first_round(lines, rx)
                    if not at:
                        continue
                    tot += 1
                    if not asked_by(says, rx, at):
                        un += 1
                cells.append(f"{un / tot:>10.1%}" if tot else f"{'—':>10s}")
            print(f"    {name[:18]:20s}{''.join(cells)}")
    return verdict


def main() -> None:
    parser = argparse.ArgumentParser(description="线索覆盖率（零模型调用，读已有语料）")
    parser.add_argument("--scenario", choices=[s.id for s in _SCENES],
                        help="只跑一个场景。不给就跑全部")
    parser.add_argument("--leak", action="store_true",
                        help="附带「未经发问的抖落」率，验判据 (a) 的前提")
    parser.add_argument("--gate", type=float, default=None,
                        help="全覆盖率门槛（0~1）。**不设默认值**，见模块文档")
    args = parser.parse_args()

    sids = [args.scenario] if args.scenario else [s.id for s in _SCENES]
    results = [report(sid, args.leak, args.gate) for sid in sids]

    if args.gate is not None:
        judged = [r for r in results if r is not None]
        if judged and all(judged):
            print(f"\n全部 {len(judged)} 个场景过门槛。")
        elif judged:
            print(f"\n{sum(1 for r in judged if not r)}/{len(judged)} 个场景未过门槛。")
        raise SystemExit(0 if judged and all(judged) else 1)


if __name__ == "__main__":
    main()
