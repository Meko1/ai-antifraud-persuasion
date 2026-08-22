"""分类器标注集跑批（§9.3）。

判分的正确性由两件事决定：规则表算得对，以及**标签判得对**。前者是纯函数，
蒙特卡洛已经守住（`tools/balance_sim.py`）；后者只有模型说了算，唯一的办法
是拿人工标注的真值去量。

本脚本**会真实调用模型**，因此不进 pytest：CI 里不该依赖外部 API，也不该
每次 push 都花钱。标注集本身的质量由 `tests/test_classify_eval.py` 守着，
那部分不调模型。

用法：
    python -m tools.classify_eval                  # 全量跑批并校验 §9.4 门槛
    python -m tools.classify_eval --limit 10       # 先花几毛钱确认链路通
    python -m tools.classify_eval --concurrency 8  # 赶时间时开大
    python -m tools.classify_eval --quiet          # 只要结论，不打印错样本

网关不可用时脚本直接抛错中止——半途失败的跑批会算出一个漂亮的低分，
让人误以为是模型判得差，其实是网断了。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from app.classify import Classification, parse_classification
from app.scoring import ALL_PENALTIES, KEY_VALUES

LABELS: Tuple[str, ...] = (*KEY_VALUES, *ALL_PENALTIES)

# 混淆矩阵里"什么都没判"的那一行／那一列。漏判与误判是两类不同的病，
# 没有它，这两种错误都会从矩阵里消失。
EMPTY = "∅"

DEFAULT_SET_PATH = Path(__file__).resolve().parent.parent / "tests" / "data" / "classification_set.jsonl"

# ── §9.4 验收门槛 ──────────────────────────────────────────────────────────
HIT_ACCURACY_FLOOR = 0.85
GROUNDED_ACCURACY_FLOOR = 0.80

# 复读攻略这一类要单独设槛，且比通用门槛严得多。
# §9.2 的结论是"没有扎根门控，攻略传开当天游戏即报废"——门控在总体准确率
# 达标的情况下照样可以从这一类上漏光，实测见 docs/TECH-DESIGN.md §9.3。
#
# 这个数字**原本**是从平衡模型反推的：parrot 标称扎根率 12%，蒙特卡洛扫过去，
# 有效扎根率一到 0.18 就击穿 parrot 胜率上限，倒推出泄漏率上限 7%。
# 难度重设计之后临界点移到了 0.40（效力矩阵与阻力曲线本身也在拦复读——
# 复读的人不会挑档位），按同样的算法反推只需 68%。
#
# 仍然守 93%，理由是它不要钱：标注集实测这一类是 100%，一分余量都没花。
# 而新腾出来的那 25 个百分点来自效力矩阵，那套参数自己还要随平衡迭代动——
# 把安全线挂在一个会动的东西上，是在借明天的余量。
PARROT_GROUNDED_FLOOR = 0.93
PARROT_TAG = "parrot"


@dataclass(frozen=True)
class Case:
    """一条人工标注。

    `context` 是上一轮劝阻对象说的话——扎根与否只能相对于它来判，
    脱离上下文的"扎根"标注没有意义。

    `sid` 是这条样本写在哪个场景的语域里。**分类器不知道剧本是什么**
    （它判的是手法），所以这一列不进提示词，只用来把准确率按场景拆开报——
    "同一套判据在四个场景上都成立"是个断言，不拆开就永远验不了它。
    """

    id: str
    tag: str
    context: str
    utterance: str
    hits: FrozenSet[str]
    grounded: bool
    sid: str = "any"
    note: str = ""


@dataclass(frozen=True)
class LabelStat:
    """单个标签的三种去向。准确率说有多差，这里说差在哪一侧。"""

    correct: int = 0   # 该判且判了
    missed: int = 0    # 该判却没判（漏判）
    spurious: int = 0  # 不该判却判了（误判）


@dataclass(frozen=True)
class Mistake:
    case: Case
    predicted: Optional[Classification]


@dataclass(frozen=True)
class Report:
    total: int
    key_cases: int  # 期望里含钥匙的样本数，即扎根真正影响判分的那部分
    hit_accuracy: float
    grounded_accuracy: float
    grounded_accuracy_on_keys: float
    unparsed: int
    confusion: Dict[str, Dict[str, int]]
    label_stats: Dict[str, LabelStat]
    # 按难例分类拆开的扎根准确率。总体数字会把某一类的塌方摊平，
    # 而复读攻略那一类塌了就等于扎根门控失效。
    grounded_accuracy_by_tag: Dict[str, float] = field(default_factory=dict)
    # 按场景拆开的 (hit_keys 准确率, grounded 准确率, 样本数)。
    #
    # "同一张判分表、同一套七把钥匙，换个骗局照样成立"是这个作品的论点，
    # 而分类器是它最靠前的一环。**这句话此前只是个断言**——标注集里
    # 荐股局占了三分之二，总体准确率再高也说明不了另外三个场景。
    # 拆开报之后它才是一条能被证伪的数。
    accuracy_by_sid: Dict[str, Tuple[float, float, int]] = field(default_factory=dict)
    mistakes: Tuple[Mistake, ...] = ()


def load_cases(path: Path = DEFAULT_SET_PATH) -> Tuple[Case, ...]:
    cases: List[Case] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        data = json.loads(line)
        cases.append(
            Case(
                id=data["id"],
                tag=data["tag"],
                context=data["context"],
                utterance=data["utterance"],
                hits=frozenset(data["hits"]),
                grounded=data["grounded"],
                sid=data.get("sid", "any"),
                note=data.get("note", ""),
            )
        )
    return tuple(cases)


def summarize(
    pairs: Iterable[Tuple[Case, Optional[Classification]]]
) -> Report:
    """把逐条结果压成 §9.3 要的准确率与混淆矩阵。

    命中的判对标准是**集合完全相等**：判分引擎吃的是整个集合，少判一个失误
    或多判一把钥匙，delta 就变了。按标签逐个算平均会把"漏判一个失误"稀释成
    5/6 分，那是自欺。
    """
    total = hit_ok = grounded_ok = unparsed = 0
    key_cases = key_grounded_ok = 0
    by_tag: Dict[str, List[int]] = {}
    by_sid: Dict[str, List[int]] = {}
    confusion = {row: {col: 0 for col in (*LABELS, EMPTY)} for row in (*LABELS, EMPTY)}
    tallies: Dict[str, List[int]] = {label: [0, 0, 0] for label in LABELS}
    mistakes: List[Mistake] = []

    for case, predicted in pairs:
        total += 1
        predicted_hits: FrozenSet[str] = (
            frozenset(predicted.hit_keys) if predicted else frozenset()
        )
        # 解析失败按判错计：分类不可降级，生产里它等于该轮记 0 分。
        # 从分母里剔掉它会把一次真实故障洗成"没发生过"。
        hits_match = predicted is not None and predicted_hits == case.hits
        grounded_match = predicted is not None and predicted.grounded == case.grounded

        hit_ok += hits_match
        grounded_ok += grounded_match
        unparsed += predicted is None

        if case.hits & frozenset(KEY_VALUES):
            key_cases += 1
            key_grounded_ok += grounded_match

        tally = by_tag.setdefault(case.tag, [0, 0])
        tally[0] += grounded_match
        tally[1] += 1

        scene_tally = by_sid.setdefault(case.sid, [0, 0, 0])
        scene_tally[0] += hits_match
        scene_tally[1] += grounded_match
        scene_tally[2] += 1

        for row in case.hits or {EMPTY}:
            for col in predicted_hits or {EMPTY}:
                confusion[row][col] += 1

        for label in LABELS:
            expected, got = label in case.hits, label in predicted_hits
            if expected and got:
                tallies[label][0] += 1
            elif expected:
                tallies[label][1] += 1
            elif got:
                tallies[label][2] += 1

        if not (hits_match and grounded_match):
            mistakes.append(Mistake(case=case, predicted=predicted))

    return Report(
        total=total,
        key_cases=key_cases,
        hit_accuracy=_ratio(hit_ok, total),
        grounded_accuracy=_ratio(grounded_ok, total),
        grounded_accuracy_on_keys=_ratio(key_grounded_ok, key_cases),
        unparsed=unparsed,
        confusion=confusion,
        label_stats={
            label: LabelStat(*counts) for label, counts in tallies.items()
        },
        grounded_accuracy_by_tag={
            tag: _ratio(ok, n) for tag, (ok, n) in sorted(by_tag.items())
        },
        accuracy_by_sid={
            sid: (_ratio(hit, n), _ratio(gnd, n), n)
            for sid, (hit, gnd, n) in sorted(by_sid.items())
        },
        mistakes=tuple(mistakes),
    )


def check_thresholds(report: Report) -> List[str]:
    """返回未通过的门槛说明；空列表表示全部通过。"""
    failures = []

    if report.hit_accuracy < HIT_ACCURACY_FLOOR:
        failures.append(
            f"hit_keys 准确率 {report.hit_accuracy:.1%} < {HIT_ACCURACY_FLOOR:.0%}"
        )

    # 两个口径任一塌了都算不过：全样本被大量纯失误样本抬上去、
    # 有钥匙子集却烂掉，是最危险的一种"通过"。
    if (
        report.grounded_accuracy < GROUNDED_ACCURACY_FLOOR
        or report.grounded_accuracy_on_keys < GROUNDED_ACCURACY_FLOOR
    ):
        failures.append(
            f"grounded 准确率不达标（全样本 {report.grounded_accuracy:.1%}，"
            f"有钥匙子集 {report.grounded_accuracy_on_keys:.1%}），"
            f"门槛 {GROUNDED_ACCURACY_FLOOR:.0%}"
        )

    parrot = report.grounded_accuracy_by_tag.get(PARROT_TAG)
    if parrot is not None and parrot < PARROT_GROUNDED_FLOOR:
        failures.append(
            f"复读攻略子集的 grounded 准确率 {parrot:.1%} < "
            f"{PARROT_GROUNDED_FLOOR:.0%}——扎根门控正在从这一类上漏，"
            "§9.2 的结论是攻略传开当天游戏即报废"
        )

    return failures


def _ratio(ok: int, total: int) -> float:
    # 分母为 0 记满分：没有样本就没有错可犯，不该拖累门槛判定
    return ok / total if total else 1.0


# ── 跑批 ───────────────────────────────────────────────────────────────────


# 网关会偶发瞬时 5xx（实测 `upstream connect error`）。生产上客户端不重试
# ——那是刻意的，演绎超时要走 L1 降级——但跑批不能因为一次抖动整批作废。
#
# 重试**只针对网络故障**，不针对解析失败：解析失败是模型真的没按格式输出，
# 在生产里等于该轮记 0 分，必须照实计入错误率（见 summarize 的注释）。
CLASSIFY_RETRIES = 2
RETRY_BACKOFF = 2.0


async def run(
    cases: Sequence[Case], gateway: object, *, concurrency: int = 4
) -> List[Tuple[Case, Optional[Classification]]]:
    """走生产同一条路：同一个网关、同一份提示词、同一个解析器。

    第 1 轮没有历史，可供扎根的只有开场白——标注集的 `context` 正是喂到
    那个位置，因此每条样本都等价于一次真实的第 1 轮分类请求。
    """
    gate = asyncio.Semaphore(concurrency)

    async def one(case: Case) -> Tuple[Case, Optional[Classification]]:
        for attempt in range(CLASSIFY_RETRIES + 1):
            try:
                async with gate:
                    raw = await gateway.classify(  # type: ignore[attr-defined]
                        utterance=case.utterance, history=(), opening=case.context
                    )
                return case, parse_classification(raw)
            except Exception as exc:  # noqa: BLE001 - 瞬时故障不该中断跑批
                if attempt == CLASSIFY_RETRIES:
                    print(f"  ! {case.id} 调用失败，计为未解析：{type(exc).__name__}",
                          flush=True)
                    return case, None
                await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
        return case, None

    return list(await asyncio.gather(*(one(c) for c in cases)))


# ── 报告 ───────────────────────────────────────────────────────────────────

_SHORT = {
    "anchor_real_purpose": "anchor",
    "socratic_question": "socratic",
    "expose_contradiction": "expose",
    "scold": "scold",
    "preach": "preach",
    "bare_assertion": "bare",
    "unlicensed_advice": "advice",
    "guaranteed_return": "guarant",
    EMPTY: EMPTY,
}


def _short(label: str) -> str:
    """混淆矩阵的列头。**闭集加标签时不能只加到 scoring.py**——
    这张表原先是写死的六项，加了合规红线之后跑批跑完 118 次调用才在
    打印那一步 KeyError，整批白跑。取不到就退回截断，宁可难看，不要炸。
    """
    return _SHORT.get(label, label[:7])


def format_report(report: Report) -> str:
    lines = [
        f"样本 {report.total} 条（其中含钥匙 {report.key_cases} 条），"
        f"解析失败 {report.unparsed} 条",
        "",
        f"hit_keys 准确率（集合完全相等） {report.hit_accuracy:>7.1%}   "
        f"门槛 {HIT_ACCURACY_FLOOR:.0%}",
        f"grounded 准确率（全样本）       {report.grounded_accuracy:>7.1%}   "
        f"门槛 {GROUNDED_ACCURACY_FLOOR:.0%}",
        f"grounded 准确率（有钥匙子集）   {report.grounded_accuracy_on_keys:>7.1%}   "
        f"门槛 {GROUNDED_ACCURACY_FLOOR:.0%}",
        "",
        "grounded 准确率按难例分类（总体数字会把某一类的塌方摊平）",
    ]
    for tag, acc in report.grounded_accuracy_by_tag.items():
        floor = PARROT_GROUNDED_FLOOR if tag == PARROT_TAG else GROUNDED_ACCURACY_FLOOR
        lines.append(f"  {tag:<18}{acc:>7.1%}   门槛 {floor:.0%}")

    if report.accuracy_by_sid:
        lines += [
            "",
            "按场景语域（「同一套判据换个骗局照样成立」这句话的证据在这一栏）",
            f"  {'场景':<8}{'样本':>6}{'hit_keys':>11}{'grounded':>11}",
        ]
        for sid, (hit, gnd, n) in report.accuracy_by_sid.items():
            lines.append(f"  {sid:<8}{n:>6}{hit:>11.1%}{gnd:>11.1%}")
        lines.append(
            "  样本少的那几行波动大，单次跑批不足以下结论（§6.4：没有 temperature，"
            "两次连跑固有差 1.3 个百分点）"
        )

    lines += [
        "",
        "逐标签",
        f"{'标签':<22}{'判对':>6}{'漏判':>6}{'误判':>6}{'召回':>8}{'精确':>8}",
        "─" * 56,
    ]
    for label in LABELS:
        s = report.label_stats[label]
        lines.append(
            f"{label:<22}{s.correct:>6}{s.missed:>6}{s.spurious:>6}"
            f"{_ratio(s.correct, s.correct + s.missed):>8.0%}"
            f"{_ratio(s.correct, s.correct + s.spurious):>8.0%}"
        )

    lines += ["", "混淆矩阵（行＝人工标注，列＝模型预测，共现计数）", ""]
    header = "".join(f"{_short(c):>10}" for c in (*LABELS, EMPTY))
    lines.append(f"{'':<22}{header}")
    for row in (*LABELS, EMPTY):
        cells = "".join(
            f"{report.confusion[row][col] or '·':>10}" for col in (*LABELS, EMPTY)
        )
        lines.append(f"{row:<22}{cells}")

    return "\n".join(lines)


def format_mistakes(report: Report) -> str:
    if not report.mistakes:
        return "无错样本。"
    lines = [f"错样本 {len(report.mistakes)} 条", ""]
    for m in report.mistakes:
        got = (
            f"{sorted(m.predicted.hit_keys)} grounded={m.predicted.grounded}"
            if m.predicted
            else "（解析失败）"
        )
        lines += [
            f"[{m.case.id}] {m.case.tag}  {m.case.utterance}",
            f"    上一轮：{m.case.context}",
            f"    人工：{sorted(m.case.hits)} grounded={m.case.grounded}",
            f"    模型：{got}",
        ]
        if m.case.note:
            lines.append(f"    标注依据：{m.case.note}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="分类器标注集跑批（会真实调用模型）")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET_PATH, help="标注集路径")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条，用于验证链路")
    parser.add_argument("--concurrency", type=int, default=4, help="并发请求数")
    parser.add_argument("--quiet", action="store_true", help="不打印错样本")
    args = parser.parse_args()

    # 延迟导入：网关会连带加载配置（缺 STATE_SIGNING_SECRET 即拒绝启动），
    # 而 summarize / check_thresholds 这些纯函数不该被这条约束拖住。
    from app.gateway import ModelGateway

    cases = load_cases(args.set)
    if args.limit:
        cases = cases[: args.limit]

    print(f"跑批 {len(cases)} 条，并发 {args.concurrency}…", flush=True)
    pairs = asyncio.run(run(cases, ModelGateway(), concurrency=args.concurrency))
    report = summarize(pairs)

    print()
    print(format_report(report))
    print()
    if not args.quiet:
        print(format_mistakes(report))

    failures = check_thresholds(report)
    if failures:
        print("§9.4 门槛未通过：")
        for f in failures:
            print(f"  ✗ {f}")
        return 1

    print("§9.4 分类器门槛全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
