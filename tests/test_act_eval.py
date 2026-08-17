"""演绎跑批的指标口径（tools/act_eval.py）。

本文件**不调模型**：它守的是"重复率""复述反问"这些词的口径，
以及回放路线本身的质量。跑批要花钱，口径错了那钱就白花——
更糟的是会拿一个假数字去判断某次提示词改动是好是坏。

与 test_classify_eval.py 是同一个分工：那边守标注集，这边守回放路线。
"""

import pytest

from app.scoring import MAX_ROUNDS, Mood
from tools.act_eval import (
    Reply,
    is_echo_question,
    load_routes,
    summarize,
)


def 台词(route: str, run: int, round_: int, *sentences: str, said: str = "随便一句") -> Reply:
    return Reply(
        route=route,
        run=run,
        round=round_,
        utterance=said,
        sentences=sentences,
        raw="".join(sentences),
    )


# ── 回放路线本身 ──────────────────────────────────────────────────────────


def test_每条路线都是完整的十二轮() -> None:
    """少一轮，最后几轮的样本量就和前面对不齐，跨轮的数字不可比。"""
    for route in load_routes():
        assert len(route.utterances) == MAX_ROUNDS, route.id
        assert len(route.moods) == MAX_ROUNDS, route.id


def test_四条路线覆盖不同的施压形状() -> None:
    """全是"教科书打法"的话，量不到路人看到的那一面——

    而首轮基线正是在路人那条路线上炸的：cold 首句重复率 30.4%，
    climb 只有 3.8%。只跑一条路线会得出"没问题"的结论。
    """
    routes = load_routes()
    assert len(routes) >= 4
    形状 = {r.id: set(r.moods) for r in routes}
    assert 形状["cold"] <= {Mood.GUARDED, Mood.IRRITATED}, "冷脸路线不该走到松动"
    assert Mood.SOFTENING in 形状["climb"], "教科书路线得能爬到松动"


def test_档位与发言数量对不上时直接报错() -> None:
    from tools.act_eval import Route

    with pytest.raises(ValueError):
        Route(id="坏的", note="", moods=(Mood.GUARDED,), utterances=("一", "二"))


# ── 复述反问 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("首句", "玩家说", "是不是"),
    [
        # 条件反射：短、问号收尾、字是从对方那儿拿的
        ("我固执？", "你怎么这么固执", True),
        ("好心？", "我是好心提醒你", True),
        ("打水漂？", "这钱打水漂你就知道了", True),
        # 带上了他自己的内容，就不再是条件反射，是真的在顶嘴
        ("我固执？你才是不懂行", "你怎么这么固执", False),
        # 没问号：那是陈述，不是把词弹回来
        ("我不固执。", "你怎么这么固执", False),
        # 问号但字不是从对方那儿来的——这是他自己的问题
        ("你算老几？", "你怎么这么固执", False),
    ],
)
def test_认出把对方的词弹回来的那种反问(首句: str, 玩家说: str, 是不是: bool) -> None:
    assert is_echo_question(首句, 玩家说) is 是不是


# ── 重复率 ────────────────────────────────────────────────────────────────


def test_首句重复与整段重复量的是两件事() -> None:
    """基线上整段重复率是 0.0%、首句重复率 11.6%——

    只看整段会得出"完全没有重样"的结论，而玩家在聊天窗口里最先看到的、
    互相截图时对上的，正是开口那一句。这条测试把这个差别钉死。
    """
    replies = [
        台词("r", 0, 1, "我跟了三个月了。", "你懂什么。"),
        台词("r", 1, 1, "我跟了三个月了。", "你少管我。"),
    ]

    report = summarize(replies, runs_per_route=2)

    assert report.duplicate_rate == 0.0
    assert report.first_dup_rate == 0.5


def test_只在同一轮内部比较() -> None:
    """第 3 轮和第 9 轮本来就该说不同的话，跨轮比是没有意义的。"""
    replies = [
        台词("r", 0, 1, "一样的话。"),
        台词("r", 1, 1, "一样的话。"),
        台词("r", 0, 2, "一样的话。"),  # 与第 1 轮撞了，但那是另一轮的事
        台词("r", 1, 2, "另一句。"),
    ]

    report = summarize(replies, runs_per_route=2)

    # 第 1 轮两遍全同（0.5）、第 2 轮两遍全不同（0.0），均值 0.25。
    # 若把四句混在一起算，会得出 0.5——凭空多出一倍的"重复"，
    # 而那一份重复只是"他在不同轮里说了同一句话"，与不重样无关
    assert report.first_dup_rate == 0.25


def test_跨路线复现认的是与玩家说什么无关的套话() -> None:
    """同一句话出现在两条不同路线里，说明它与玩家说了什么无关——

    这类句子是"AI 味"里最致命的一种：不管你怎么劝，他都会说这一句。
    """
    replies = [
        台词("甲", 0, 1, "王老师从来没错过。"),
        台词("乙", 0, 1, "王老师从来没错过。"),
        台词("丙", 0, 1, "这句只出现在一条路线里。"),
    ]

    report = summarize(replies, runs_per_route=1)

    assert report.cross_route_repeat == pytest.approx(2 / 3)


def test_书面语按句计一次() -> None:
    """一句里踩了两个书面语词，算一句坏句，不算两句——
    分母是句子数，重复计数会让命中率超过 100%。"""
    replies = [
        台词("r", 0, 1, "然而因此我不同意。", "这句是干净的。"),
    ]

    report = summarize(replies, runs_per_route=1)

    assert report.formal_rate == 0.5


def test_括号动作按轮计且看的是模型原样吐出来的() -> None:
    """安全层会把括号剥掉，玩家看不到——所以只能从 raw 里量。

    从过完安全层的句子里量，这个指标永远是 0，等于没有。
    """
    replies = [
        Reply(
            route="r", run=0, round=1, utterance="随便",
            sentences=("行吧。",), raw="（叹了口气）行吧。",
        ),
        台词("r", 0, 2, "没有动作描写。"),
    ]

    report = summarize(replies, runs_per_route=1)

    assert report.stage_direction_rate == 0.5
