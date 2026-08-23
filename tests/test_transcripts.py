"""对局留存（ADR-0006）。

三件事要钉住，缺一件那条 ADR 就只是一篇散文：

1. **默认关**——不显式打开就一条不存，行为与 ADR-0003 时代一模一样
2. **落库前脱敏**，且脱敏发生在留存层内部（只有一个入口，就只有一处会漏）
3. **告知与开关同步**：留存开着的时候，用户开口之前看到的那句话必须说到
   存什么、存多久、拿来干什么

第 3 条是这份测试里最要紧的一条。文案和行为分两处维护，早晚会走散，
而走散的那一天，页面上写的就是一句假话。
"""

from fastapi.testclient import TestClient

from app.main import app
from app.transcripts import (
    RETENTION_DAYS,
    TranscriptStore,
    build_entry,
    disclosure,
)


def _entry(**overrides):
    base = dict(
        gid="abc123",
        sid="chen",
        round_=3,
        utterance="您手机 13812345678 是本人的吗",
        reply="你问这个干啥",
        hits=["socratic_question"],
        grounded=True,
        delta=7,
        judged_mood="irritated",
        efficacy=1.4,
        degraded=False,
    )
    base.update(overrides)
    return build_entry(**base)


class Test落库前脱敏:
    def test_玩家发言过脱敏(self):
        assert "13812345678" not in _entry()["utterance"]
        assert "[手机号]" in _entry()["utterance"]

    def test_模型台词也过脱敏(self):
        # 台词那一侧同样要过：安全层拦的是"下发给玩家"，
        # 落库是另一条路，它不经过安全层
        entry = _entry(reply="你加我微信 laochen_888 说")
        assert "laochen_888" not in entry["reply"]

    def test_判分结果原样落(self):
        entry = _entry()
        assert entry["hits"] == ["socratic_question"]
        assert entry["grounded"] is True
        assert entry["delta"] == 7
        assert entry["efficacy"] == 1.4
        assert entry["judged_mood"] == "irritated"

    def test_降级轮要标出来(self):
        """不标的话，它在语料里和"玩家说了句废话"长得一模一样。"""
        assert _entry(degraded=True, hits=[], delta=0)["degraded"] is True


class Test范围:
    """**没有的字段比有的字段更要紧**（ADR-0006 §范围）。"""

    def test_字段清单是封闭的(self):
        assert set(_entry()) == {
            "v", "gid", "sid", "round", "utterance", "reply",
            "hits", "grounded", "delta", "judged_mood", "efficacy",
            "degraded", "ts",
        }

    def test_不含账号持仓设备等任何一侧的字段(self):
        # 转向之后触发这段对话的是一条真实资产异动，把它一起存下来是最自然
        # 不过的下一步，也正是 ADR-0006 划死的那条线
        forbidden = {
            "account", "uid", "user_id", "holdings", "amount",
            "device", "ip", "phone", "incident",
        }
        assert not (forbidden & set(_entry()))


class Test默认关:
    def test_没打开就是空操作(self):
        store = TranscriptStore("redis://127.0.0.1:6379/0", enabled_flag=False)
        assert store.enabled is False

    def test_打开了但没配redis也不存(self):
        store = TranscriptStore("", enabled_flag=True)
        assert store.enabled is False


class Test告知:
    def test_关着的时候不提留存(self):
        text = disclosure(enabled=False, offline=False)
        assert "留存" not in text
        # 关着也要说清另外三件事，这一条 8-22 就在了，不能因为改留存把它弄丢
        assert "虚构" in text and "投资建议" in text and "大模型" in text

    def test_开着的时候必须说到存什么存多久干什么(self):
        text = disclosure(enabled=True)
        assert "留存" in text
        assert str(RETENTION_DAYS) in text, "存多久要写出具体天数，不能只说'一段时间'"
        assert "改进" in text, "用途要写出来"
        assert "账号" in text, "更该说清楚的是哪些东西不会被记录"

    def test_天数与代码里那个常量同源(self):
        """文案里的天数不许手写。手写的那一份改不动 TTL，只会骗人。"""
        assert f"{RETENTION_DAYS} 天" in disclosure(enabled=True)

    def test_跟着真实行为走而不是跟着开关走(self):
        """**判据是"到底存没存"，不是"开关拨到哪一边"。**

        `TRANSCRIPT_RETENTION=true` 但没配 REDIS_URL 时，一条都存不下去。
        这时候还照着开关说"会留存 90 天"，是在为一件没发生的事道歉——
        而下一次有人照着这句话去查库，会以为数据丢了。
        """
        from app import transcripts as mod

        assert mod.TranscriptStore("", enabled_flag=True).enabled is False
        assert disclosure(enabled=mod.transcripts.enabled) == disclosure()


class Test告知出现在用户开口之前:
    def test_开局响应就带着它(self):
        """"之前"是字面意思：它随 /api/game/start 下发，不是等玩家发完第一句
        才补一个弹窗，也不是藏在某个链接后面。"""
        resp = TestClient(app).post("/api/game/start")

        assert resp.status_code == 200
        assert resp.json()["notice"] == disclosure()
