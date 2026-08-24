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
            # 2026-08-23 加的三段元数据。加它们的理由写在 build_entry 的
            # 文档字符串里，一句话：**没有这三段，这批语料谁都不敢用**
            "idem",          # 幂等键，重复写入要能认出来
            "expires_at",    # 逐条到期时间，导出之后也带着
            "data_mode",     # live / offline_demo / degraded
            "label_source",  # 标签是模型给的还是规则给的还是程序填的
            "review",        # 标注生命周期，当前一律 pending
            "evidence",      # 模型说它扎根在哪一句上，给标注人员当线索
            "versions",      # 规则 / 提示词 / 场景 / 分类器各自的版本
            "origin",        # 哪条异动、哪个实验组
        }

    def test_不含账号持仓设备等任何一侧的字段(self):
        # 转向之后触发这段对话的是一条真实资产异动，把它一起存下来是最自然
        # 不过的下一步，也正是 ADR-0006 划死的那条线
        forbidden = {
            "account", "uid", "user_id", "holdings", "amount",
            "device", "ip", "phone", "incident",
        }
        assert not (forbidden & set(_entry()))

    def test_origin只放关联键不放交易细节(self):
        """**加 origin 是为了算实验效果，不是为了把那笔交易存下来。**

        `transaction_ref`（真实交易号）留在服务端事件表里，一个字都不进语料：
        它是可以拿去别处用的东西，而这份语料将来要交给标注人员看。
        """
        from app.state_token import Origin

        entry = _entry(origin=Origin(
            anomaly_id="AN-1", subject_ref="u_x", arm="intervention",
            source="upstream", trigger_type="full_liquidation",
        ))
        assert set(entry["origin"]) == {
            "anomaly_id", "arm", "trigger_type", "source"
        }
        assert "transaction_ref" not in entry["origin"]
        # subject_ref 也不进：它是跨局可关联的用户标识，而语料这一侧
        # 明确不做跨局关联（build_entry 顶部关于 gid 的那段）
        assert "subject_ref" not in entry["origin"]


class Test标签出处:
    """`hits` / `grounded` 是**模型的预测**，不是事实。不标出来，
    下一个人会拿模型自己的预测去训模型自己。"""

    def test_默认标成模型预测且待审(self):
        entry = _entry()
        assert entry["label_source"] == "model"
        assert entry["review"] == "pending"

    def test_降级轮的空标签不算模型给的(self):
        """分类超时那一轮的空 hits 是**程序按中性填的**。

        记成 `model` 等于往训练集里塞一批"模型认为这句话什么都不是"的假样本。
        """
        assert _entry(degraded=True, hits=[])["label_source"] == "degraded_neutral"

    def test_当前系统一条金标都产生不了(self):
        """这正是它该返回 False 的原因，不是一个需要绕过去的麻烦。"""
        from app.provenance import trainable

        assert trainable(_entry()) is False

    def test_版本齐全(self):
        v = _entry()["versions"]
        assert set(v) == {"app", "rules", "prompt", "scenario", "classifier"}
        assert all(v.values()), "版本号不许是空串——空串等于没记"


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

    def test_不许承诺脱敏能力做不到的事(self):
        """**告知不能超过实际能力。**

        原文是「账号、持仓与身份信息不会被记录」——一句关于**结果**的承诺，
        而实际能力只是一层正则（app/redact.py）。地址、单位、持仓名称、
        带空格的号码全都漏。改成"我们不会主动记录 + 你输入的会去标识后留存"，
        承诺从结果退回动作，同时把用户该注意的那一半说清楚。

        这条测试钉的是**不许再改回那句绝对化的承诺**。
        """
        text = disclosure(enabled=True)
        assert "不会被记录" not in text, (
            "这是一句做不到的承诺：正则脱敏挡不住地址、单位、持仓与带空格的号码"
        )
        assert "不会主动记录" in text, "能承诺的只有'我们不主动去取'"
        assert "去标识" in text, "用户自己打进去的那部分要说清是怎么处理的"
        assert "不要" in text, "做不到的部分要给用户一句可执行的提醒"

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
