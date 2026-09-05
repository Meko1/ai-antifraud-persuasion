"""离线演示模式（PIVOT-C-END §4 第 2 条）。

要钉的是三件事，一件比一件重要：

1. **一个网络请求都不发**——不然这个模式就是个摆设，网关停了照样停
2. **判分一格都不打折**——这才是它值得演的理由（ADR-0001：判分归纯函数）
3. **藏不住**——健康检查上报着、聊天页第一行写着。
   一个看不出来是演示的演示，不是演示，是一场骗局
"""

import asyncio
import json

import pytest

from app.fallback import AVOID_WINDOW, fallback_line
from app.offline import (
    OFFLINE_NOTE,
    OfflineGateway,
    classify_offline,
    grounded_offline,
)
from app.scenario import DEFAULT
from app.scoring import Ending, Mood


def _run(coro):
    return asyncio.run(coro)


class Test一个请求都不发:
    def test_演绎不碰网络(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """把 httpx 与 anthropic 两条路都掐掉，还能演出台词才算数。"""
        import httpx

        def boom(*_args, **_kwargs):
            raise AssertionError("离线模式发了网络请求")

        monkeypatch.setattr(httpx, "AsyncClient", boom)

        async def collect():
            return "".join(
                [chunk async for chunk in OfflineGateway().act(mood=Mood.GUARDED)]
            )

        assert _run(collect())

    def test_分类不碰网络(self) -> None:
        raw = _run(
            OfflineGateway().classify(
                utterance="这笔钱本来是打算做什么用的？", opening="你少管我"
            )
        )
        parsed = json.loads(raw)
        assert parsed["hit_keys"] == ["anchor_real_purpose"]
        assert "grounded" in parsed

    def test_结局不碰网络(self) -> None:
        assert _run(OfflineGateway().narrate_ending(ending=Ending.PERSUADED))


class Test形状与线上网关一致:
    """接口对不上的话，离线模式会在演示当天才炸——那是它最没用的时候。"""

    def test_三个操作都在(self) -> None:
        from app.gateway import ModelGateway

        for name in ("act", "classify", "narrate_ending"):
            assert hasattr(OfflineGateway(), name)
            assert hasattr(ModelGateway(), name)

    def test_分类返回的是可解析的闭集JSON(self) -> None:
        from app.classify import parse_classification

        raw = _run(OfflineGateway().classify(utterance="您是不是傻", opening="随便你"))
        parsed = parse_classification(raw)
        assert parsed is not None
        assert "scold" in parsed.hit_keys

    def test_演绎产出的是文本增量(self) -> None:
        async def collect():
            return [c async for c in OfflineGateway().act(mood=Mood.IRRITATED, scene=DEFAULT)]

        chunks = _run(collect())
        assert len(chunks) > 1, "一次性整句下发的话，离线模式的节奏与线上差得最远"
        assert "".join(chunks) in DEFAULT.lines[Mood.IRRITATED]


class Test不许说最近说过的话:
    """看得出来的重复，是唯一能一秒钟推翻「劝阻对象是活的」的东西。

    原来是无记忆的 `random.choice`，每档 10 条；玩家在同一档位连坐三五轮
    是常态（阻力曲线要求如此）。实测一局打满（12 轮口径，蒙特卡洛 2 万次）：

        窗口   紧邻重复    3 轮内重复
        0       56.9%      72.9%     ← 原状
        1        0.0%      37.4%
        2        0.0%       0.0%     ← 现在
        3        0.0%       0.0%     ← 一句都不多赚

    而离线模式正是 README 里写着"路演用它"的那个模式。
    """

    def test_取词时排除上一句(self) -> None:
        lines = list(DEFAULT.lines[Mood.GUARDED])
        上一句 = lines[0]
        # 跑够多次：这一条要证的是"永远不会"，不是"多半不会"
        got = {
            fallback_line(Mood.GUARDED, scene=DEFAULT, avoid=上一句)
            for _ in range(400)
        }
        assert 上一句 not in got, "又把上一句原样说了一遍"
        assert len(got) > 1, "排除之后退化成了固定的一句，那是另一种重复"

    def test_整档被排干净也得给得出一句(self) -> None:
        """它是"绝不给玩家一片空白"那一层，任何情况下都必须有话说。"""
        整档 = "".join(DEFAULT.lines[Mood.GUARDED])
        assert fallback_line(Mood.GUARDED, scene=DEFAULT, avoid=整档) in DEFAULT.lines[
            Mood.GUARDED
        ]

    def test_窗口是最近两句不是一句(self) -> None:
        """1 只压得住紧邻重复，隔一轮又说同一句照样刺眼。

        实测（每档停留 3 轮，蒙特卡洛 2 万次）：窗口 1 时"3 轮内重复"
        仍有 37.4%，窗口 2 降到 0.0%，窗口 3 一句都不多赚。
        """
        assert AVOID_WINDOW == 2, "改这个数之前先看 fallback_line 里那张实测表"

    def test_离线网关自己把最近那两句喂进去(self) -> None:
        """光有参数不算数——`act` 得真的从 history 里把它们取出来。"""

        class _轮:
            def __init__(self, reply: str) -> None:
                self.reply = reply

        最近两句 = [
            _轮(DEFAULT.lines[Mood.IRRITATED][0]),
            _轮(DEFAULT.lines[Mood.IRRITATED][1]),
        ]
        # 再塞一条更早的，确认窗口**只取最近两句**，不是把整局都排除掉
        history = [_轮(DEFAULT.lines[Mood.IRRITATED][2]), *最近两句]

        async def once():
            return "".join(
                [
                    c
                    async for c in OfflineGateway().act(
                        mood=Mood.IRRITATED, scene=DEFAULT, history=history
                    )
                ]
            )

        said = {_run(once()) for _ in range(500)}
        for 轮 in 最近两句:
            assert 轮.reply not in said, "网关没把 history 里最近那两句都用上"
        assert history[0].reply in said, (
            "窗口开得太大：再往前的台词也被排除了，"
            "一局下来会把一个档位的池子掏空"
        )


class Test关键词分类:
    """粗得毫不掩饰，够走完一局就行。真准确率是模型的活（§9.3）。"""

    def test_路演最常打的那一句判得对(self) -> None:
        # 里面一个"钱"字都没有，第一版因此把它判成了普通提问。
        # 演示当天被打出来的概率最高的就是这一句
        assert classify_offline("陈叔，这三十万本来是打算做什么用的？") == [
            "anchor_real_purpose"
        ]

    def test_钥匙最多记一把(self) -> None:
        # 闭集里七把钥匙最多只记一个（消歧规则第 1 条）
        hits = classify_offline("这笔钱本来是做什么用的？您自己讲一遍好吗？")
        keys = [h for h in hits if h in {
            "anchor_real_purpose", "socratic_question", "expose_contradiction",
            "reflect_feeling", "support_autonomy", "check_understanding",
            "informed_warning",
        }]
        assert len(keys) == 1

    def test_合规红线与钥匙可以并存(self) -> None:
        hits = classify_offline("我保证您跟着我做稳赚，这笔钱本来是做什么用的？")
        assert "guaranteed_return" in hits
        assert "anchor_real_purpose" in hits

    def test_扎根要真的引用上一轮(self) -> None:
        assert grounded_offline("他不收手续费，那他赚谁的钱", "王老师从来没跟我要过一分钱手续费")
        assert not grounded_offline("这笔钱本来是做什么用的", "王老师从来没跟我要过一分钱手续费")

    def test_没有上一轮就不算扎根(self) -> None:
        assert not grounded_offline("随便说点什么", "")


class Test藏不住:
    def test_健康检查上报(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        body = TestClient(app).get("/healthz").json()
        assert "offline_demo" in body, "运维唯一会看的那一处必须报出来"

    def test_开局那句告知带着它(self) -> None:
        assert "离线演示" in OFFLINE_NOTE
        assert "不调用大模型" in OFFLINE_NOTE
        # 光说"是演示"不够，还要说清哪一半是真的——判分那一半
        assert "判分" in OFFLINE_NOTE

    def test_告知里要说清命中判定也降级了(self) -> None:
        """**只说"判分仍由规则表算"会让人读出一个不成立的结论。**

        判分是纯函数没错，可这时候喂给它的标签来自 `_KEY_RULES` 那张
        关键词表，不是模型。留出集实测（`python -m tools.offline_eval`）：
        完全命中 56.9%，真值非空却一条没判中的仍占 38.9%——
        模型那边的门槛是 85%。

        也就是每五句仍有两句被判成"钥匙一把都没沾上"，而复盘整页
        都建在这些标签上——README 又写着"路演用它"。
        **在代码注释里承认过，不等于对用户告知过。**

        准确率的下限由 `tests/test_offline_eval.py` 守着，这一条只管
        "有没有说出口"。
        """
        assert "关键词" in OFFLINE_NOTE, "没说命中判定换成了关键词规则"
        assert "准确率" in OFFLINE_NOTE, "没说这一换意味着什么"

    def test_那句告知不自相矛盾(self) -> None:
        """**"数据去向"那一段是替换，不是追加。**

        第一版是拼接，于是页面上先说"你输入的内容会发送至大模型"、
        再说"不调用大模型"。两句话打架的时候，用户只会记住错的那一句。
        """
        from app.transcripts import disclosure

        text = disclosure(enabled=False, offline=True)
        assert "离线演示" in text
        assert "发送至大模型" not in text
        # 虚构与非投资建议那两条与联不联网无关，任何模式下都不能丢
        assert "虚构" in text and "投资建议" in text

    def test_默认关着(self) -> None:
        from app.config import settings

        assert settings.offline_demo is False
