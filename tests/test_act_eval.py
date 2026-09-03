"""演绎跑批的指标口径（tools/act_eval.py）。

本文件**不调模型**：它守的是"重复率""复述反问"这些词的口径，
以及回放路线本身的质量。跑批要花钱，口径错了那钱就白花——
更糟的是会拿一个假数字去判断某次提示词改动是好是坏。

与 test_classify_eval.py 是同一个分工：那边守标注集，这边守回放路线。
"""

import pytest

from app.scenario import SCENARIOS
from app.scoring import MAX_ROUNDS, Mood
from tools.act_eval import (
    RATE_LIMIT_BACKOFF,
    RETRY_BACKOFF,
    Reply,
    _backoff_for,
    is_echo_question,
    load_routes,
    summarize,
)

场景 = tuple(s.id for s in SCENARIOS)


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
        assert len(route.utterances) == MAX_ROUNDS, f"{route.sid}/{route.id}"
        assert len(route.moods) == MAX_ROUNDS, f"{route.sid}/{route.id}"


@pytest.mark.parametrize("sid", 场景)
def test_每个场景都有自己的四条路线(sid: str) -> None:
    """**8-22 之前只有荐股局那四条**，而跑批脚本结构上也只跑得了老陈。

    于是"演绎基线"这个词历史上只对一个场景成立。拿"王老师带您做的那只票"
    去问周淑琴，量出来的是她在答非所问，三条门槛全部失去意义。
    """
    routes = load_routes(sid=sid)

    assert {r.id for r in routes} == {"cold", "climb", "sawtooth", "parrot"}


@pytest.mark.parametrize("sid", 场景)
def test_四条路线覆盖不同的施压形状(sid: str) -> None:
    """全是"教科书打法"的话，量不到路人看到的那一面——

    而首轮基线正是在路人那条路线上炸的：cold 首句重复率 30.4%，
    climb 只有 3.8%。只跑一条路线会得出"没问题"的结论。
    """
    形状 = {r.id: set(r.moods) for r in load_routes(sid=sid)}

    assert 形状["cold"] <= {Mood.GUARDED, Mood.IRRITATED}, "冷脸路线不该走到松动"
    assert Mood.SOFTENING in 形状["climb"], "教科书路线得能爬到松动"


def test_教科书路线必须按各场景自己的最优解写() -> None:
    """climb 是"效力矩阵会翻过来"在演绎侧的对照组。

    照抄老陈那条过去，量的就变成了"玩家在这个场景里打错了"——那是路线的
    毛病，不是演绎的毛病，而报告上分不出这两件事。这里只做一件最机械的
    检查：四个场景的 climb 发言不许有任何一条重合。
    """
    climbs = {
        sid: set(next(r for r in load_routes(sid=sid) if r.id == "climb").utterances)
        for sid in 场景
    }

    for a in 场景:
        for b in 场景:
            if a < b:
                assert not climbs[a] & climbs[b], f"{a} 与 {b} 的教科书路线撞了"


def test_教科书路线走遍四个档位() -> None:
    """每个场景的四把冠军钥匙分落在四个不同的档位上。

    所以一条自称"教科书"的路线必须四档都走到——少走一档，那一档的冠军
    在这一批里就没有出场机会，而那正是"换了场景最优解会翻过来"这件事
    在演绎侧最该被看见的地方。

    这条只检查**结构**（档位轨迹），发言内容靠人看，路线的 note 里写着
    每一档配的是哪一把。
    """
    for scene in SCENARIOS:
        climb = next(r for r in load_routes(sid=scene.id) if r.id == "climb")

        assert set(climb.moods) == set(Mood), (
            f"{scene.id} 的教科书路线没走到 {set(Mood) - set(climb.moods)}"
        )


def test_四个场景的冠军钥匙确实不是同一组() -> None:
    """"换个骗局最优解要翻过来"是这个作品的论点，路线是照它写的。

    冠军按**得分**算（基值 × 效力），不是按倍率。踩过一次坑：倍率最高的
    往往是基值最低的那把（reflect 基值 8，倍率 2.4 也才 19.2 分，
    照倍率排会把四个场景的最优解全读错，路线也就跟着写错）。
    """
    from app.scoring import KEY_VALUES

    def 冠军(scene: object) -> tuple:
        eff = scene.efficacy  # type: ignore[attr-defined]
        return tuple(
            max(KEY_VALUES, key=lambda k: KEY_VALUES[k] * eff[k][mood])
            for mood in (Mood.GUARDED, Mood.IRRITATED, Mood.WAVERING, Mood.SOFTENING)
        )

    组 = {scene.id: 冠军(scene) for scene in SCENARIOS}

    assert len(set(组.values())) == len(SCENARIOS), f"有两个场景的最优解一模一样：{组}"


def test_复读机路线四个场景共用同一组发言() -> None:
    """复读攻略的定义就是"贴到任何一段对话里都成立"。

    它不带任何场景成分，因此四个场景的玩家输入完全相同——
    四份指标的差异 100% 来自演绎本身。这是唯一一条纯净的跨场景对照，
    改动它等于拆掉这个对照。
    """
    parrots = {
        next(r for r in load_routes(sid=sid) if r.id == "parrot").utterances
        for sid in 场景
    }

    assert len(parrots) == 1


def test_档位与发言数量对不上时直接报错() -> None:
    from tools.act_eval import Route

    with pytest.raises(ValueError):
        Route(
            id="坏的", sid="chen", note="",
            moods=(Mood.GUARDED,), utterances=("一", "二"),
        )


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


# ── 跑批容错 ──────────────────────────────────────────────────────────────


def test_限流要退到下一个分钟窗口_其余错误照旧() -> None:
    """RPM 是按分钟算的窗口，2 秒和 4 秒两次重试全落在同一个窗口里。

    真实错误体长这样：
    `RateLimitError: Error code: 429 - {... 该令牌对模型的 RPM 已经到达上限 ...}`
    """
    限流 = RuntimeError(
        "流式调用失败: RateLimitError: Error code: 429 - "
        "{'error': {'message': '该令牌对模型的RPM已经到达上限，当前值 31，RPM限制 30'}}"
    )
    瞬时故障 = RuntimeError(
        "upstream connect error or disconnect/reset before headers"
    )

    # 抖动是乘上去的（1.0~1.5 倍），所以比区间而不是比等号
    for attempt in range(2):
        长 = _backoff_for(限流, attempt)
        短 = _backoff_for(瞬时故障, attempt)
        assert RATE_LIMIT_BACKOFF * (attempt + 1) <= 长 <= RATE_LIMIT_BACKOFF * (attempt + 1) * 1.5
        assert RETRY_BACKOFF * (attempt + 1) <= 短 <= RETRY_BACKOFF * (attempt + 1) * 1.5
        assert 长 > 短, "限流退得必须比瞬时故障久，否则退了等于没退"


def test_退避带抖动_并发几路不会一起醒() -> None:
    """不抖的话并发的几路会同时撞限流、同时退同样久、同时醒——
    等于并发数从没降下来过，下一轮再一起撞一次。
    """
    限流 = RuntimeError("RateLimitError: 429")
    样本 = {_backoff_for(限流, 0) for _ in range(50)}

    assert len(样本) > 1, "退避是个定值，几路会一直同步"
