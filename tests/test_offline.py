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
