"""离线关键词分类器的跑批。

## 它和 `classify_eval` 的分工

同一份标注集（`tests/data/classification_set.jsonl`），两个被测对象：

* `classify_eval` 量的是**模型**。要花钱、要网络，因此不进 pytest。
* 这一份量的是 `app/offline.py` 那张关键词表。纯函数、零依赖，
  所以它**可以**进 CI，也应该进——见 `tests/test_offline_eval.py`。

## 为什么值得单独量

离线模式是 README 里写着"路演用它"的那个模式，而 2026-08-31 第一次量它：

    完全命中 29.6%    真值非空却一条没判中 67.3%

也就是评委在演示现场每打三句，有两句会被判成"钥匙一把都没沾上"，
而复盘整页都建在这些标签上。这张表**本来就该粗**（它顶不了模型的活），
但"粗"和"三分之二判漏"是两回事，后者会让演示本身失去说服力。

## 留出集：这份脚本最重要的一段

199 条样本，规则是手写的正则——**直接对着全集调，必然过拟合**。
调到 90% 只说明我把这 199 条背下来了，说明不了演示现场那句没见过的话。

所以按 `id` 的哈希切两半（`--split`）：

* **dev（约 2/3）** —— 允许看错例、允许照着改规则
* **holdout（约 1/3）** —— 调规则期间一次都不看，最后只报一个数

两个数之间的差就是过拟合的量。**报告里两个都印**，只印 dev 那个是自欺。

切分用 `sha1(id)` 而不是行号或随机种子：往标注集里加样本时，
已有样本的归属**不会跟着变**，昨天的 holdout 今天还是 holdout。

用法：
    python -m tools.offline_eval                # 两半都报
    python -m tools.offline_eval --split dev    # 只看 dev（调规则时）
    python -m tools.offline_eval --show-errors  # 打印错例，按标签分组
"""

from __future__ import annotations

import argparse
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Sequence, Tuple

from app.offline import classify_offline, grounded_offline
from app.scoring import ALL_PENALTIES, KEY_VALUES
from tools.classify_eval import Case, load_cases

LABELS: Tuple[str, ...] = (*KEY_VALUES, *ALL_PENALTIES)

# holdout 占比。1/3 —— 再小就撑不住每个标签几条样本，再大 dev 上就调不动。
_HOLDOUT_SHARE = 3


def split_of(case_id: str) -> str:
    """这条样本归 dev 还是 holdout。

    **只认 id，不认行号也不认顺序。** 加样本、删样本、重排文件都不会让
    已有样本换边——否则"holdout 我没看过"这句话隔一次提交就不成立了。
    """
    digest = hashlib.sha1(case_id.encode("utf-8")).hexdigest()
    return "holdout" if int(digest, 16) % _HOLDOUT_SHARE == 0 else "dev"


@dataclass(frozen=True)
class LabelStat:
    correct: int = 0
    missed: int = 0
    spurious: int = 0

    @property
    def recall(self) -> float:
        want = self.correct + self.missed
        return self.correct / want if want else 1.0

    @property
    def precision(self) -> float:
        got = self.correct + self.spurious
        return self.correct / got if got else 1.0


@dataclass(frozen=True)
class Result:
    total: int
    exact: float          # 预测集合与真值集合完全相同
    blind: float          # 真值非空却一条都没判中（漏光）
    spurious: float       # 判出了真值里没有的标签
    grounded: float
    label_stats: Dict[str, LabelStat]
    mistakes: Tuple[Tuple[Case, FrozenSet[str]], ...]


def evaluate(cases: Sequence[Case]) -> Result:
    exact = blind = spurious = grounded_ok = 0
    stats: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"correct": 0, "missed": 0, "spurious": 0}
    )
    mistakes: List[Tuple[Case, FrozenSet[str]]] = []

    for case in cases:
        got = frozenset(classify_offline(case.utterance))
        want = case.hits
        if got == want:
            exact += 1
        else:
            mistakes.append((case, got))
        if want and not (got & want):
            blind += 1
        if got - want:
            spurious += 1
        for label in LABELS:
            if label in want and label in got:
                stats[label]["correct"] += 1
            elif label in want:
                stats[label]["missed"] += 1
            elif label in got:
                stats[label]["spurious"] += 1
        if grounded_offline(case.utterance, case.context) == case.grounded:
            grounded_ok += 1

    n = len(cases) or 1
    return Result(
        total=len(cases),
        exact=exact / n,
        blind=blind / n,
        spurious=spurious / n,
        grounded=grounded_ok / n,
        label_stats={k: LabelStat(**v) for k, v in stats.items()},
        mistakes=tuple(mistakes),
    )


def _print(name: str, r: Result) -> None:
    print(f"\n── {name}（{r.total} 条）" + "─" * 40)
    print(f"  完全命中        {r.exact:6.1%}")
    print(f"  一条都没判中     {r.blind:6.1%}   ← 屏幕上写「钥匙一把都没沾上」的那一批")
    print(f"  判出了多余的标签  {r.spurious:6.1%}")
    print(f"  扎根判断        {r.grounded:6.1%}")
    print()
    print(f"  {'标签':<22}{'查全':>8}{'查准':>8}   该判 / 漏判 / 误判")
    for label in LABELS:
        s = r.label_stats.get(label)
        if not s or (s.correct + s.missed + s.spurious) == 0:
            continue
        want = s.correct + s.missed
        print(f"  {label:<22}{s.recall:>7.0%}{s.precision:>8.0%}   "
              f"{want:>4} / {s.missed:>4} / {s.spurious:>4}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", choices=("dev", "holdout", "both"), default="both")
    ap.add_argument("--show-errors", action="store_true")
    ap.add_argument("--tag", help="只看某一类难例（plain / parrot / tricky_wording / false_friend）")
    args = ap.parse_args(argv)

    cases = load_cases()
    if args.tag:
        cases = tuple(c for c in cases if c.tag == args.tag)

    groups = {"dev": [], "holdout": []}
    for c in cases:
        groups[split_of(c.id)].append(c)

    names = ["dev", "holdout"] if args.split == "both" else [args.split]
    results = {}
    for name in names:
        results[name] = evaluate(groups[name])
        _print(name, results[name])

    if args.split == "both":
        d, h = results["dev"], results["holdout"]
        gap = d.exact - h.exact
        print(f"\n  dev − holdout = {gap:+.1%}   "
              f"（这个差就是过拟合的量；holdout 那个数才是能对外说的）")

    if args.show_errors:
        for name in names:
            print(f"\n── {name} 错例 " + "─" * 46)
            by_label: Dict[str, List[str]] = defaultdict(list)
            for case, got in results[name].mistakes:
                key = "、".join(sorted(case.hits)) or "∅"
                by_label[key].append(
                    f"    [{case.id} {case.tag}] {case.utterance}\n"
                    f"      真值 {sorted(case.hits) or '∅'}  →  判成 {sorted(got) or '∅'}"
                    + (f"\n      注：{case.note}" if case.note else "")
                )
            for key in sorted(by_label, key=lambda k: -len(by_label[k])):
                print(f"\n  真值 = {key}（{len(by_label[key])} 条）")
                for line in by_label[key]:
                    print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
