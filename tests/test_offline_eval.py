"""离线关键词分类器的准确率下限。

## 为什么这组测试要存在

`app/offline.py` 那张表是**路演当天真正在判分的东西**（README：「路演用它」）。
2026-08-31 第一次量它：完全命中 29.6%，**真值非空却一条都没判中的占 67.3%**
——评委每打三句，有两句屏幕上写「钥匙一把都没沾上」，而复盘整页都建在
这些标签上。

那张表当时已经写着"粗得毫不掩饰"。**在注释里承认过，不等于有人在守着它**：
没有任何一条测试量过它，所以它烂到 29.6% 也没有人知道。这组测试就是那个
缺失的看守。

## 数字为什么分两套报

规则是手写的正则，标注集只有 199 条——**直接对着全集调必然过拟合**。
`tools.offline_eval` 因此按 `sha1(id)` 把样本切成 dev / holdout 两半，
调规则只看 dev。实测两个数差得很远：

    第一版重写后     dev 90.6%   holdout 44.4%   （差 46 个点）
    按语言类别泛化后  dev 89.0%   holdout 56.9%   （差 32 个点）

**下面的门槛压在 holdout 上**，不压在 dev 上，也不压在全集上——
只有 holdout 那个数说得了"没见过的话判得怎么样"。

## 门槛为什么定在这儿

留的余量比较宽（holdout 命中 50% 而实测 56.9%），因为这几个数会随着
标注集加样本而抖动，而**这组测试要拦的是"有人把规则改窄了"这种塌方**，
不是三五个百分点的浮动。塌方长什么样有过实例：改之前是 23.6%。
"""

from __future__ import annotations

import pytest

from app.offline import classify_offline, grounded_offline
from tools.classify_eval import load_cases
from tools.offline_eval import evaluate, split_of

# ── 门槛 ──────────────────────────────────────────────────────────────────
#
# holdout 上的两个主指标。`BLIND` 是这组里最要紧的一个：
# 它直接对应"屏幕上写「钥匙一把都没沾上」"的比例，也就是演示现场
# 玩家打了一句好话却什么都没拿到的概率。
HOLDOUT_EXACT_FLOOR = 0.50
HOLDOUT_BLIND_CEIL = 0.45

# 全集只作为参考量，门槛给得更松——它含着调过的那一半，本来就偏高。
FULL_EXACT_FLOOR = 0.72

# 扎根。与模型那侧同一个数（tools/classify_eval.py 的 GROUNDED_ACCURACY_FLOOR）。
GROUNDED_FLOOR = 0.80

# 复读攻略那一类的扎根准确率，**比通用门槛严得多**，理由与模型那侧一字不差：
# §9.2 的结论是"没有扎根门控，攻略传开当天游戏即报废"，而门控可以在总体
# 准确率达标的情况下从这一类上漏光。
PARROT_GROUNDED_FLOOR = 0.93


@pytest.fixture(scope="module")
def cases():
    return load_cases()


def _split(cases, name):
    return [c for c in cases if split_of(c.id) == name]


class Test没见过的话也得判得动:
    def test_holdout_完全命中不低于门槛(self, cases) -> None:
        r = evaluate(_split(cases, "holdout"))
        assert r.exact >= HOLDOUT_EXACT_FLOOR, (
            f"留出集完全命中掉到 {r.exact:.1%}（门槛 {HOLDOUT_EXACT_FLOOR:.0%}）。"
            "跑 `python -m tools.offline_eval --split dev --show-errors` 看错例"
        )

    def test_holdout_判漏不超过上限(self, cases) -> None:
        """**这一条是这组里最要紧的。**

        `blind` = 真值非空却一条都没判中，也就是屏幕上写「钥匙一把都没沾上」。
        它塌了，演示现场就会出现"玩家说了一句标准的反映式倾听、界面告诉他
        什么都没做对"——而这正是 2026-08-31 之前每三句里有两句的状况。
        """
        r = evaluate(_split(cases, "holdout"))
        assert r.blind <= HOLDOUT_BLIND_CEIL, (
            f"留出集判漏涨到 {r.blind:.1%}（上限 {HOLDOUT_BLIND_CEIL:.0%}）"
        )

    def test_全集完全命中不低于门槛(self, cases) -> None:
        r = evaluate(cases)
        assert r.exact >= FULL_EXACT_FLOOR, f"全集完全命中 {r.exact:.1%}"


class Test扎根门控:
    def test_总体扎根准确率(self, cases) -> None:
        r = evaluate(cases)
        assert r.grounded >= GROUNDED_FLOOR, f"扎根准确率 {r.grounded:.1%}"

    def test_复读攻略那一类不许漏(self, cases) -> None:
        """模板句被判成"扎根"，扎根门控就等于没有。

        量的是**这一个方向**：真值 false 却判成 true。反过来（真的扎根了
        没算上）是玩家吃亏，讨厌但不致命；这一头漏了，攻略传开当天游戏报废。
        """
        parrot = [c for c in cases if c.tag == "parrot"]
        对 = sum(
            1 for c in parrot
            if grounded_offline(c.utterance, c.context) == c.grounded
        )
        漏 = [
            c for c in parrot
            if grounded_offline(c.utterance, c.context) and not c.grounded
        ]
        assert 对 / len(parrot) >= PARROT_GROUNDED_FLOOR, (
            f"复读那一类扎根准确率 {对 / len(parrot):.1%}，"
            f"其中被判成扎根的模板句 {len(漏)} 条：\n"
            + "\n".join(f"    [{c.id}] {c.utterance[:40]}" for c in 漏[:5])
        )


class Test路演现场那几句:
    """**不取自标注集**，是照着演示会被打出来的样子现写的。

    标注集上的数字再好看，也可能是把那 199 条背下来了。这一组问的是
    另一件事：**一个正常人在演示现场随手打一句，判得中吗。**
    十二把闭集标签，一把一句。
    """

    样本 = [
        ("anchor_real_purpose", "刘叔，这笔钱您当初存进来的时候，是打算给谁用的？"),
        ("support_autonomy", "我不拦您，钱是您自己的，转不转您说了算。"),
        ("check_understanding", "您跟我讲一遍，这钱转过去之后是谁在操作？"),
        ("reflect_feeling", "我听得出来您心里其实是有数的，也不好意思往回退。"),
        (
            "expose_contradiction",
            "您上次说这钱是给孙子留的学费，今天却全划到一个陌生账户，这两件事我对不上。",
        ),
        ("socratic_question", "前三波赚的钱，现在还在您自己账户里吗？"),
        ("informed_warning", "正规渠道不会让您把钱打到个人账户，所以我认为这是个局。"),
        ("bare_assertion", "这就是诈骗。"),
        ("scold", "您这么大岁数了，怎么连这点判断力都没有？"),
        ("preach", "我跟你讲，这种套路分三步，你先听我说完。"),
        ("unlicensed_advice", "我建议您把仓位清了，换成低风险的产品。"),
        ("guaranteed_return", "跟着我做，保证您不亏。"),
    ]

    @pytest.mark.parametrize("want,utterance", 样本)
    def test_判得中(self, want: str, utterance: str) -> None:
        got = classify_offline(utterance)
        assert want in got, f"「{utterance}」判成了 {got}"


class Test被否定掉的贬低词不是贬低:
    """**这一类在反诈劝阻里不是边角情况。**

    「被骗不丢人」「您不糊涂」是这个场景里最该说的一类话，而它们逐字
    包含着责骂词表里的词。两条都是 2026-08-31 在浏览器里真打出来撞见的，
    不是想出来的：

        「我听出来您心里其实是有数的，也不好意思往回退。这不丢人。」
          → 判成 scold，一句教科书级的反映式倾听被记成责骂

    反过来，**A-不-A 反问里的那个"不"不是否定**——「您是不是傻」正是
    最典型的责骂形态之一，第一版否定闸把它也放走了。
    """

    @pytest.mark.parametrize(
        "utterance",
        [
            "我听出来您心里其实是有数的，也不好意思往回退。这不丢人。",
            "被骗不丢人，说出来才有人帮得上。",
            "您不糊涂，您只是没被人这么骗过。",
        ],
    )
    def test_安慰不算责骂(self, utterance: str) -> None:
        assert "scold" not in classify_offline(utterance), f"「{utterance}」被记了责骂"

    @pytest.mark.parametrize("utterance", ["您是不是傻？", "你有没有脑子？"])
    def test_反问照样算责骂(self, utterance: str) -> None:
        assert "scold" in classify_offline(utterance), f"「{utterance}」没记责骂"
