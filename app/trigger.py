"""开局上下文：这次干预是被什么触发的。

## 它解决的问题

在此之前 `/api/game/start` 不收任何参数：服务端生成一个随机 `gid`，
场景由 `gid` 的哈希派生，页面上写着"客户由系统随机分配"。
于是定位文档里的"清仓""大额转出""历史行为显著背离"**只是剧本布景，
不是系统的触发条件**——文档转了，运行时没转。

这个模块把那条线接上：场景由**真实异动类型**在服务端选，不由随机数选。

## 它不解决的问题（写在明处）

**上游那条真实异动流水不在这个仓库里，接不进来。** 这里能做到的是
把契约定死、把选择逻辑搬到服务端、把"这是演示"和"这是真实异动"
在数据里分开。真实的资产异动、真实的交易号、真实的 24 小时后结果，
必须由宿主 App 和交易系统提供。

所以 `StartContext.source` 有两个值，而且**演示态是显式的**：

- `upstream` —— 调用方传了完整的异动上下文（真实接入）
- `demo`     —— 没传，服务端合成了一个（大赛演示、本地开发）

合成的那一份一样会走完整条链路，但它带着 `source="demo"`，
统计与语料因此可以把它整个排除掉。**一个看不出来是演示的演示是骗局**——
离线模式那一条已经这么定过一次，这里是同一条原则。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from .provenance import SCENARIO_VERSION
from .scenario import DEFAULT, SCENARIOS, Scenario


class TriggerType(str, Enum):
    """资产异动的类型。闭集——未知值在入口就被拒掉，不落到某个默认档。

    这五种对应券商真实能观测到的异动，也是定位文档里点名的那几种。
    """

    FULL_LIQUIDATION = "full_liquidation"      # 清仓：全部持仓卖出
    LARGE_TRANSFER_OUT = "large_transfer_out"  # 大额转出
    FUND_REDEMPTION = "fund_redemption"        # 基金 / 理财全额赎回
    UNUSUAL_PAYEE = "unusual_payee"            # 新增收款方 / 首次转入陌生账户
    BEHAVIOR_DEVIATION = "behavior_deviation"  # 历史行为显著背离


class Arm(str, Enum):
    """实验分组。

    `CONTROL` 组在真实接入里**根本不该看到这个干预**——它走现行的风险提示弹窗。
    这个仓库里没有那个弹窗，所以对照组在这里只是一个被记下来的分组标记；
    真正的分流要发生在宿主 App 那一层。记它的理由是：分组必须在
    **干预发生之前**就定下来并留痕，事后再分组的实验没有可信度。
    """

    CONTROL = "control"
    INTERVENTION = "intervention"


# ── 异动类型 → 候选场景 ───────────────────────────────────────────────────
#
# **这是这个模块的核心，也是"场景不再由随机数决定"的落点。**
#
# 挑选依据是剧本里那笔钱**怎么动的**，必须和用户自己刚做的动作对得上：
# 一个刚赎回全部基金的人，去劝一个"清空持仓准备转账"的人，
# 两件事在他眼里不是一回事，角色对调那个想法就落空了。
#
#   chen  荐股群      清空持仓 → 转账给指定账户
#   zhou  冒充公检法   赎回全部基金 → 转到"安全账户"
#   liu   杀猪盘      赎回理财 → 转到"投资平台"
#   ben   刷单返利    清空账户 → 垫付本金
#   hang  虚拟币量化   清仓持仓 → 转入交易所
_CANDIDATES: Dict[TriggerType, Tuple[str, ...]] = {
    TriggerType.FULL_LIQUIDATION: ("chen", "hang"),
    TriggerType.FUND_REDEMPTION: ("zhou", "liu"),
    TriggerType.LARGE_TRANSFER_OUT: ("chen", "ben"),
    TriggerType.UNUSUAL_PAYEE: ("ben", "liu"),
    # 行为背离是个兜底型信号，它不指向某一种资金动作，所以候选是全集
    TriggerType.BEHAVIOR_DEVIATION: tuple(s.id for s in SCENARIOS),
}

_BY_ID = {s.id: s for s in SCENARIOS}


@dataclass(frozen=True)
class StartContext:
    """一次干预的全部上下文。**每个字段都要能在事件表里被关联和去重。**

    `anomaly_id` 是主键：同一条异动重复弹出干预，必须能认出来是同一条。
    """

    anomaly_id: str
    trigger_type: TriggerType
    # 交易号或赎回指令号。干预结束后要凭它回到那笔真实交易，
    # 也要凭它去问交易系统"24 小时之后这笔到底取消了没有"
    transaction_ref: str
    # 用户的匿名标识。**不是用户 ID**：它由宿主 App 用自己的盐哈希之后给我们，
    # 这一侧永远拿不到可以反查到人的东西
    subject_ref: str
    arm: Arm
    triggered_at: int
    source: str  # "upstream" | "demo"
    scenario_version: str = SCENARIO_VERSION
    # 场景由服务端按 trigger_type 选定，选完写在这里，随令牌走
    sid: str = ""

    def to_event(self) -> Dict[str, Any]:
        """事件表里的那一份。**不含任何自由文本**，也不含金额。"""
        return {
            "anomaly_id": self.anomaly_id,
            "trigger_type": self.trigger_type.value,
            "transaction_ref": self.transaction_ref,
            "subject_ref": self.subject_ref,
            "arm": self.arm.value,
            "triggered_at": self.triggered_at,
            "source": self.source,
            "scenario_version": self.scenario_version,
            "sid": self.sid,
        }


class TriggerError(ValueError):
    """上下文非法。**在入口就拒绝**，不猜一个最接近的类型。"""


def _digest(*parts: str) -> int:
    raw = ":".join(parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def scenario_for_trigger(trigger: TriggerType, anomaly_id: str) -> Scenario:
    """按异动类型选场景。

    **在候选里的选择由 `anomaly_id` 决定，不由随机数决定。** 这一条不是洁癖：
    同一条异动被重复弹出（用户刷新、App 重启、干预中断后回来）必须落到
    同一个场景，否则用户会看到客户凭空换了一个人，而实验数据里
    同一条异动会横跨两个场景。
    """
    ids = _CANDIDATES.get(trigger) or tuple(s.id for s in SCENARIOS)
    picked = ids[_digest(anomaly_id, "scenario") % len(ids)]
    return _BY_ID.get(picked, DEFAULT)


def _trigger_for_sid(sid: str) -> TriggerType:
    """场景 → 异动类型。`scenario_for_trigger` 的反向，只给演示态挑客户时用。

    一个场景可能落在多个异动类型下（老陈既可以是清仓也可以是大额转出），
    取第一个匹配的即可——演示态记的这条异动本来就是合成的，
    要紧的是它和场景**自洽**，不是它精确。
    """
    for trigger, ids in _CANDIDATES.items():
        if sid in ids and trigger is not TriggerType.BEHAVIOR_DEVIATION:
            return trigger
    return TriggerType.BEHAVIOR_DEVIATION


def catalog() -> Tuple[Dict[str, Any], ...]:
    """可选客户清单。开局屏那个"换一位客户"要用。

    只给名字与一句话——**不给难度、不给胜率、不给效力矩阵**。
    那些是答案，和 primer 里 `brief` / `tip` 的分界是同一条：
    挑客户是在选题目，不是在挑一道已经知道答案的题。
    """
    return tuple(
        {
            "id": s.id,
            "name": s.name,
            "peer": s.peer,
            "client": s.client_name,
            "headline": s.incident_title,
            "trigger_type": _trigger_for_sid(s.id).value,
        }
        for s in SCENARIOS
    )


def assign_arm(subject_ref: str, *, experiment: str = "roleswap-v1") -> Arm:
    """实验分组。

    **由匿名用户标识哈希决定，不掷骰子**：同一个人重复触发必须落在同一组，
    否则同一个人会既进干预组又进对照组，实验直接失效。

    `experiment` 进哈希是为了让下一个实验重新洗牌——沿用同一个分组的话，
    上一个实验的效应会渗进下一个实验。
    """
    return Arm.INTERVENTION if _digest(subject_ref, experiment) % 2 else Arm.CONTROL


def build_context(payload: Optional[Dict[str, Any]] = None) -> StartContext:
    """从请求体拼一个上下文。**给了就严格校验，没给就合成一个演示态的。**

    没给的时候不抛错，理由是这个作品当前的主要形态就是演示：
    大赛评委打开首页时没有任何上游系统在给它喂异动。但合成出来的那一份
    **必须自曝身份**（`source="demo"`），否则演示数据会混进业务口径。
    """
    data = payload or {}

    # ── 演示态 ────────────────────────────────────────────────────────────
    #
    # 判据是"**一个上游字段都没带**"，不是"请求体空不空"，也不是
    # "有没有 anomaly_id"。演示态可以带一个 `sid`（用户自己挑了客户），
    # 那仍然是演示态。
    #
    # **这里不能只看 anomaly_id。** 只看它的话，一个接入方漏传 anomaly_id
    # 的请求会被安安静静地当成演示态处理——数据照落，但整批带着
    # `source="demo"`，于是被业务口径整个排除掉，而没有任何一处报过错。
    # 那正是这次复核要消灭的那类静默兜底。
    UPSTREAM_FIELDS = ("anomaly_id", "trigger_type", "transaction_ref",
                       "subject_ref", "arm", "triggered_at")
    if not any(data.get(f) for f in UPSTREAM_FIELDS):
        # `anomaly_id` 随机，于是不挑的时候场景仍然一局一换，但走的是
        # **和真实接入完全相同的那条选择逻辑**——演示和生产共用一条代码路径，
        # 生产那条才不会长年没人走过。
        anomaly_id = uuid.uuid4().hex
        subject = uuid.uuid4().hex

        # **挑客户只在演示态成立。** 真实接入时场景由用户自己那笔异动决定，
        # 让他挑等于让他挑一个和自己处境无关的剧本，角色对调就落空了。
        # 演示态没有"他自己那笔异动"这回事，挑一个反而是对的：
        # 评委想再看一遍某个场景，不该靠反复重开去抽。
        chosen = str(data.get("sid", "")).strip().lower()
        if chosen and chosen not in _BY_ID:
            raise TriggerError(
                f"sid={chosen!r} 不是已知场景，允许的是 {sorted(_BY_ID)}"
            )
        if chosen:
            # 挑定之后 trigger_type 反推回去：场景与异动类型必须自洽，
            # 否则事件表里会出现"清仓触发了一个赎回剧本"这种对不上的记录
            trigger = _trigger_for_sid(chosen)
        else:
            trigger = list(TriggerType)[_digest(anomaly_id, "trigger") % len(TriggerType)]

        return _finish(
            anomaly_id=anomaly_id,
            trigger=trigger,
            transaction_ref="",
            subject_ref=subject,
            arm=Arm.INTERVENTION,
            triggered_at=int(time.time()),
            source="demo",
            sid=chosen,
        )

    raw_type = str(data.get("trigger_type", "")).strip().lower()
    try:
        trigger = TriggerType(raw_type)
    except ValueError as exc:
        raise TriggerError(
            f"trigger_type={raw_type!r} 不是合法的异动类型，"
            f"允许的是 {[t.value for t in TriggerType]}"
        ) from exc

    anomaly_id = str(data.get("anomaly_id", "")).strip()
    if not anomaly_id:
        raise TriggerError("缺少 anomaly_id：同一条异动重复弹出时要靠它去重")

    subject_ref = str(data.get("subject_ref", "")).strip()
    if not subject_ref:
        raise TriggerError("缺少 subject_ref：没有它就没法分组，也没法算二次触发率")

    # 分组允许上游指定（宿主 App 那一层已经分好了），没指定就在这里算。
    # **两种都要留痕**，事后才说得清这一局是谁分的组
    raw_arm = str(data.get("arm", "")).strip().lower()
    if raw_arm:
        try:
            arm = Arm(raw_arm)
        except ValueError as exc:
            raise TriggerError(f"arm={raw_arm!r} 不是合法的分组") from exc
    else:
        arm = assign_arm(subject_ref)

    triggered_at = int(data.get("triggered_at") or time.time())

    return _finish(
        anomaly_id=anomaly_id,
        trigger=trigger,
        transaction_ref=str(data.get("transaction_ref", "")).strip(),
        subject_ref=subject_ref,
        arm=arm,
        triggered_at=triggered_at,
        source="upstream",
    )


def _finish(
    *,
    anomaly_id: str,
    trigger: TriggerType,
    transaction_ref: str,
    subject_ref: str,
    arm: Arm,
    triggered_at: int,
    source: str,
    sid: str = "",
) -> StartContext:
    """补上服务端定的那两项：场景与场景版本。

    **场景在这里定，不在前端定，也不由 gid 派生。** 这是 P0-1 的落点。
    """
    return StartContext(
        anomaly_id=anomaly_id,
        trigger_type=trigger,
        transaction_ref=transaction_ref,
        subject_ref=subject_ref,
        arm=arm,
        triggered_at=triggered_at,
        source=source,
        sid=sid or scenario_for_trigger(trigger, anomaly_id).id,
    )
