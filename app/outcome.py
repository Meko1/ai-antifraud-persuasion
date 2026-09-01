"""结局回传契约：宿主 App 把"24 小时后这笔到底完成没有"报回来。

## 它解决的问题

`app/trigger.py` 一路带着 `anomaly_id` / `transaction_ref` / `subject_ref` / `arm`
走完开局、状态令牌、统计分桶，却没有任何接口告诉宿主 App 该往哪儿把
"24 小时后的结果"传回来。核心指标（24h 内转出放弃率，见
docs/POSITIONING.md「成功标准」）因此即使真接了异动流水也算不出来——
没有回传，就没有分子。这个模块补的是这一段缺口。

## 它不解决的问题（写在明处）

真实的"24 小时后这笔到底完成没有"这件事本身，仍然要由宿主 App 或交易
系统自己去查、去判断。这里只接收判断结果，不产生判断，也不替它去问
交易系统——那条访问权限这个仓库本来就不该有。

## 为什么不需要新的服务端状态（ADR-0003 同一条原则）

`arm`（对照组/干预组）不需要宿主 App 在回传时重复告诉我们——它本来就是
`subject_ref` 的确定性哈希（`trigger.assign_arm`），报回来的时候现算一遍
即可，不用在开局那一刻另存一份"这个 anomaly_id 属于哪个 arm"的映射表。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from .config import settings
from .trigger import Arm, TriggerType, assign_arm

logger = logging.getLogger(__name__)

try:  # 与 stats / guard 共用同一条依赖判断
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - 取决于部署环境装没装
    Redis = None  # type: ignore[assignment]

_NS = "ai-antifraud-persuasion"

# Redis 卡住时不能把请求拖住，与 stats / guard 用同一个预算
_TIMEOUT = 0.5


class OutcomeStatus(str, Enum):
    """24 小时窗口结束后，这笔异动对应的转出/赎回到底怎么样了。"""

    ABANDONED = "abandoned"  # 窗口内没有再完成——本作品的核心指标数的是这个
    COMPLETED = "completed"  # 窗口内还是完成了——干预没拦住
    UNKNOWN = "unknown"      # 交易系统那边也说不清（账户注销、数据缺失等）


class OutcomeError(ValueError):
    """回传的字段不合法。在入口就拒绝，不猜一个最接近的状态——与
    `trigger.TriggerError` 同一条原则。"""


@dataclass(frozen=True)
class OutcomeReport:
    """一条回传。`anomaly_id` 认哪条异动，`subject_ref` 用来现算 `arm`。"""

    anomaly_id: str
    subject_ref: str
    status: OutcomeStatus
    observed_at: int
    window_hours: int = 24
    # 可选：宿主愿意给就给，用来按异动类型拆分；不给不影响 arm 这条主线
    trigger_type: str = ""

    @property
    def arm(self) -> Arm:
        """现算，不依赖任何服务端存下来的映射（ADR-0003）。"""
        return assign_arm(self.subject_ref)

    def to_event(self) -> Dict[str, Any]:
        """事件表里的那一份。**不含任何自由文本**，也不含金额。"""
        return {
            "anomaly_id": self.anomaly_id,
            "subject_ref": self.subject_ref,
            "status": self.status.value,
            "observed_at": self.observed_at,
            "window_hours": self.window_hours,
            "trigger_type": self.trigger_type,
            "arm": self.arm.value,
        }


def parse_report(payload: Optional[Dict[str, Any]]) -> OutcomeReport:
    """从请求体解析一份回传。缺字段或非法值一律拒绝，不猜一个最接近的。"""
    data = payload or {}

    anomaly_id = str(data.get("anomaly_id", "")).strip()
    if not anomaly_id:
        raise OutcomeError("缺少 anomaly_id：不知道这份回传对应哪一条异动")

    subject_ref = str(data.get("subject_ref", "")).strip()
    if not subject_ref:
        raise OutcomeError("缺少 subject_ref：算不出 arm，这份回传就没法归类")

    raw_status = str(data.get("status", "")).strip().lower()
    try:
        status = OutcomeStatus(raw_status)
    except ValueError as exc:
        raise OutcomeError(
            f"status={raw_status!r} 不是合法取值，允许的是 "
            f"{[s.value for s in OutcomeStatus]}"
        ) from exc

    observed_at = int(data.get("observed_at") or time.time())
    window_hours = int(data.get("window_hours") or 24)

    # 可选字段，但给了就必须是闭集里的值——**不猜一个最接近的**，
    # 与 trigger.build_context 对 trigger_type 的态度同一条原则。
    # 不校验的话，一个拼错的异动类型会在 Redis 里悄悄开一个新键，
    # 按类型拆分的统计就此永久碎成两份，没有任何一处报过错。
    raw_trigger_type = str(data.get("trigger_type", "")).strip()
    trigger_type = ""
    if raw_trigger_type:
        try:
            trigger_type = TriggerType(raw_trigger_type.lower()).value
        except ValueError as exc:
            raise OutcomeError(
                f"trigger_type={raw_trigger_type!r} 不是合法的异动类型，"
                f"允许的是 {[t.value for t in TriggerType]}"
            ) from exc

    return OutcomeReport(
        anomaly_id=anomaly_id,
        subject_ref=subject_ref,
        status=status,
        observed_at=observed_at,
        window_hours=window_hours,
        trigger_type=trigger_type,
    )


class OutcomeStore:
    """回传结果的存储。**与 `stats.Stats` 不同：这里的写入失败要让调用方
    知道**——统计丢一笔不影响任何一局对局，可以悄悄吞掉；回传丢一笔，
    这条 24 小时观测就永久没有第二个来源了，宿主 App 需要知道要不要重试。
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Optional[Any] = None

    @property
    def enabled(self) -> bool:
        return bool(self._url) and Redis is not None

    def _conn(self) -> Any:
        if self._client is None:
            from .stats import force_db0

            self._client = Redis.from_url(
                force_db0(self._url),
                socket_timeout=_TIMEOUT,
                socket_connect_timeout=_TIMEOUT,
                decode_responses=True,
            )
        return self._client

    async def record(self, report: OutcomeReport) -> bool:
        """记一笔回传。返回是否真的写进去了——**这是唯一一个 stats 模块
        风格的方法里，失败需要变成调用方能看到的返回值的地方**。
        """
        if not self.enabled:
            return False
        try:
            key = f"{_NS}:outcomes:{report.arm.value}"
            await self._conn().hincrby(key, report.status.value, 1)
            if report.trigger_type:
                trig_key = f"{_NS}:outcomes:by_trigger:{report.trigger_type}"
                await self._conn().hincrby(trig_key, report.status.value, 1)
            return True
        except Exception as exc:  # noqa: BLE001 - 连不上时如实报失败，不装作成功
            logger.warning("回传写入失败: %s", exc)
            return False

    async def snapshot(self) -> Dict[str, Any]:
        """读不到就返回 available=false，不编造零值——道理与
        `stats.Stats.snapshot` 相同。"""
        if not self.enabled:
            return {"available": False}
        try:
            conn = self._conn()
            pipe = conn.pipeline()
            pipe.hgetall(f"{_NS}:outcomes:{Arm.INTERVENTION.value}")
            pipe.hgetall(f"{_NS}:outcomes:{Arm.CONTROL.value}")
            intervention, control = await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("回传读取失败: %s", exc)
            return {"available": False}
        return {
            "available": True,
            "intervention": {k: int(v) for k, v in (intervention or {}).items()},
            "control": {k: int(v) for k, v in (control or {}).items()},
        }


# 单例。与 `stats` / `guard` 同一个做法：连接惰性建立，
# 导入本模块不产生任何网络行为。
outcome_store = OutcomeStore(settings.redis_url)
