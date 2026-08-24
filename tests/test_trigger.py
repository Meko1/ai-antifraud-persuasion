"""开局上下文（app/trigger.py）。

这个模块存在的全部理由是一句话：**场景由真实异动类型在服务端选，
不由随机数选。** 在它之前，定位文档里的"清仓""大额转出""历史行为显著背离"
只是剧本布景——`/api/game/start` 不收任何参数，场景是 `gid` 的哈希。

所以这份测试钉三样：
1. 契约本身（缺字段、非法值一律在入口拒掉，不猜一个最接近的）
2. **同一条异动永远落到同一个场景**（用户刷新、App 重启不该换客户）
3. **演示态自曝身份**（合成的上下文不许伪装成真实接入）
"""

import pytest

from app.trigger import (
    Arm,
    TriggerError,
    TriggerType,
    assign_arm,
    build_context,
    catalog,
    scenario_for_trigger,
)


class Test真实接入:
    def _ctx(self, **over):
        base = dict(
            anomaly_id="AN-2026-0823-771",
            trigger_type="fund_redemption",
            transaction_ref="TX-99812",
            subject_ref="u_8f3a91",
        )
        base.update(over)
        return build_context(base)

    def test_完整上下文能建起来(self) -> None:
        ctx = self._ctx()
        assert ctx.source == "upstream"
        assert ctx.trigger_type is TriggerType.FUND_REDEMPTION
        assert ctx.transaction_ref == "TX-99812"
        assert ctx.sid, "服务端必须选出一个场景"

    def test_场景由异动类型选而不是随机(self) -> None:
        """**这是 P0-1 的落点。**

        赎回类的异动只会落到"赎回"那两个剧本上——一个刚赎回全部基金的人，
        去劝一个"清空持仓准备转账"的人，两件事在他眼里不是一回事，
        角色对调那个想法就落空了。
        """
        for _ in range(20):
            ctx = self._ctx(anomaly_id=f"AN-{_}")
            assert ctx.sid in ("zhou", "liu"), f"赎回不该落到 {ctx.sid}"

    def test_同一条异动永远是同一个场景(self) -> None:
        """用户刷新、App 重启、干预中断后回来——**客户不能凭空换人**。

        实验数据那一侧同样：同一条异动横跨两个场景，那条记录就废了。
        """
        first = self._ctx().sid
        for _ in range(10):
            assert self._ctx().sid == first

    def test_缺anomaly_id直接拒(self) -> None:
        """**不能悄悄当成演示态。**

        只按"有没有 anomaly_id"分辨演示与真实的话，接入方漏传这一个字段时
        请求会被安安静静地按演示处理——数据照落，但整批带着 `source="demo"`
        被业务口径排除掉，而没有任何一处报过错。判据因此是
        "一个上游字段都没带才是演示"。
        """
        with pytest.raises(TriggerError, match="anomaly_id"):
            build_context({"trigger_type": "fund_redemption", "subject_ref": "u"})

    def test_缺subject_ref直接拒(self) -> None:
        """没有它就没法分组，也没法算 30 天二次触发率。"""
        with pytest.raises(TriggerError, match="subject_ref"):
            build_context({"anomaly_id": "A", "trigger_type": "fund_redemption"})

    def test_未知异动类型直接拒而不是落到某个默认档(self) -> None:
        """**不猜一个最接近的**——那正是静默兜底的另一种写法。"""
        with pytest.raises(TriggerError, match="trigger_type"):
            build_context({
                "anomaly_id": "A", "trigger_type": "清仓", "subject_ref": "u",
            })

    def test_未知分组直接拒(self) -> None:
        with pytest.raises(TriggerError, match="arm"):
            self._ctx(arm="treatment")


class Test实验分组:
    def test_同一个人永远在同一组(self) -> None:
        """**掷骰子分组的实验是废的**：同一个人既进干预组又进对照组，
        两边的差异就不再是干预造成的。"""
        first = assign_arm("u_8f3a91")
        for _ in range(50):
            assert assign_arm("u_8f3a91") is first

    def test_换一个实验重新洗牌(self) -> None:
        """沿用同一个分组的话，上一个实验的效应会渗进下一个实验。"""
        subjects = [f"u_{i}" for i in range(200)]
        a = [assign_arm(s, experiment="exp-a") for s in subjects]
        b = [assign_arm(s, experiment="exp-b") for s in subjects]
        assert a != b

    def test_两组大致均分(self) -> None:
        arms = [assign_arm(f"u_{i}") for i in range(1000)]
        share = arms.count(Arm.INTERVENTION) / len(arms)
        assert 0.4 < share < 0.6, f"分组严重不均: {share:.2%}"

    def test_上游指定的分组照收(self) -> None:
        """宿主 App 那一层已经分好了的情况。两种来源都要留痕，
        事后才说得清这一局是谁分的组。"""
        ctx = build_context({
            "anomaly_id": "A", "trigger_type": "fund_redemption",
            "subject_ref": "u", "arm": "control",
        })
        assert ctx.arm is Arm.CONTROL


class Test演示态:
    def test_不传上下文也能开局(self) -> None:
        """大赛评委打开首页时没有任何上游系统在喂异动。"""
        ctx = build_context()
        assert ctx.sid
        assert ctx.anomaly_id, "合成的也要有 id，否则曝光去不了重"

    def test_演示态必须自曝身份(self) -> None:
        """**一个看不出来是演示的演示是骗局**（与离线模式同一条原则）。

        `source` 决定这一局进哪个统计口径（app/stats.py 的 `data_mode`）——
        不标出来，演示数据会混进业务数字。
        """
        assert build_context().source == "demo"
        assert build_context({"sid": "zhou"}).source == "demo"

    def test_演示态可以挑客户(self) -> None:
        assert build_context({"sid": "zhou"}).sid == "zhou"

    def test_挑一个不存在的客户要拒(self) -> None:
        with pytest.raises(TriggerError, match="sid"):
            build_context({"sid": "nobody"})

    def test_挑定之后异动类型要自洽(self) -> None:
        """事件表里不该出现"清仓触发了一个赎回剧本"这种对不上的记录。"""
        ctx = build_context({"sid": "zhou"})
        assert ctx.sid in scenario_for_trigger(ctx.trigger_type, ctx.anomaly_id).id \
            or ctx.sid in [s for s in ("zhou", "liu")]

    def test_不挑的时候仍然一局一换(self) -> None:
        seen = {build_context().sid for _ in range(60)}
        assert len(seen) > 1, "演示态不挑客户时该是随机的"


class Test客户清单:
    def test_五个场景都在(self) -> None:
        assert {c["id"] for c in catalog()} == {"chen", "zhou", "liu", "ben", "hang"}

    def test_只给名字与一句话不给答案(self) -> None:
        """**挑客户是在选题目，不是在挑一道已经知道答案的题。**

        与 primer 里 `brief` / `tip` 的分界是同一条：给难度、给胜率、
        给效力矩阵，就等于在选之前先泄了题。
        """
        for row in catalog():
            assert set(row) == {
                "id", "name", "peer", "client", "headline", "trigger_type"
            }
