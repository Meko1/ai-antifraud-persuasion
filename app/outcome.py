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

# 幂等去重键的存活时间。**不跟 window_hours 挂钩**：这条键防的是"宿主
# 重试"，不是"观测窗口本身"——重试可能发生在窗口关闭之后（宿主那边的
# 补录、重跑批一样不保证卡在 24 小时内）。7 天给重试留够余量，同时不让
# 这些键在 Redis 里无限攒下去。
_DEDUP_TTL_SECONDS = 7 * 24 * 3600


class OutcomeStatus(str, Enum):
    """24 小时窗口结束后，这笔异动对应的转出/赎回到底怎么样了。"""

    ABANDONED = "abandoned"  # 窗口内没有再完成——本作品的核心指标数的是这个
    COMPLETED = "completed"  # 窗口内还是完成了——干预没拦住
    UNKNOWN = "unknown"      # 交易系统那边也说不清（账户注销、数据缺失等）


class OutcomeError(ValueError):
    """回传的字段不合法。在入口就拒绝，不猜一个最接近的状态——与
    `trigger.TriggerError` 同一条原则。"""


class RecordResult(str, Enum):
    """`OutcomeStore.record` 的结果。**四种取值分四种响应**，不能再拿一个
    `bool` 糊过去——"存上了"和"没存但不是错"是两件事，混成一个 `False`
    会让宿主重试出的重复回传和真正的存储故障走同一条错误处理路径。
    """

    STORED = "stored"          # 第一次见到这条 (anomaly_id, window_hours)
    DUPLICATE = "duplicate"    # 见过，状态跟上次一样——重试，不是新数据
    CONFLICT = "conflict"      # 见过，状态跟上次不一样——拒绝，不覆盖
    UNAVAILABLE = "unavailable"  # Redis 不可用，宿主应该重试


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

    async def record(self, report: OutcomeReport) -> RecordResult:
        """记一笔回传。**幂等**：同一个 `(anomaly_id, window_hours)` 重复
        上报，状态跟上次一样就返回 `DUPLICATE`（不重复计数），状态跟上次
        不一样就返回 `CONFLICT`（同样不计数，也不覆盖）——宿主重试或者
        宿主自己数据出错，都不该悄悄污染分子。

        去重键只挂 `anomaly_id` 与 `window_hours`，不挂 `arm`：`arm` 由
        `subject_ref` 现算，同一个 `anomaly_id` 不可能在两次回传里算出两个
        `arm`，带上它只是多一个永远不变的维度。

        **原子性用 `SET NX EX` 一条命令换**，与 `guard.claim` 同一个手法：
        谁先抢到这条 key，谁的状态才算数。抢到之后再去 `HINCRBY` 计数器——
        这两步不是一个事务，`SET` 成功后 `HINCRBY` 那一下如果恰好断连，
        这条计数会永久漏记（后续重试会被 `SET NX` 挡在外面，判成
        `DUPLICATE`）。**这个窗口比"先计数再声明"更安全**：后者失败时是
        重复计数，会直接破坏"重复上报只计一条"这条要保证的东西；前者失败时
        只是漏记一条，不违反这条不变量。两条命令之间的断连概率极低，且这里
        是统计旁路而非资金过账，用 Lua 脚本把两步焊成一个原子操作换来的确定性，
        不值得为一个从未出现过的边界引入这仓库里第一处 `EVAL`。
        """
        if not self.enabled:
            return RecordResult.UNAVAILABLE
        dedup_key = f"{_NS}:outcomes:seen:{report.anomaly_id}:{report.window_hours}"
        try:
            claimed = await self._conn().set(
                dedup_key, report.status.value, nx=True, ex=_DEDUP_TTL_SECONDS
            )
            if not claimed:
                previous = await self._conn().get(dedup_key)
                if previous == report.status.value:
                    return RecordResult.DUPLICATE
                logger.warning(
                    "回传冲突: anomaly_id=%s window_hours=%s 已记为 %r，本次又报 %r",
                    report.anomaly_id, report.window_hours,
                    previous, report.status.value,
                )
                return RecordResult.CONFLICT

            pipe = self._conn().pipeline()
            pipe.hincrby(f"{_NS}:outcomes:{report.arm.value}", report.status.value, 1)
            if report.trigger_type:
                trig_key = (
                    f"{_NS}:outcomes:by_trigger:"
                    f"{report.trigger_type}:{report.arm.value}"
                )
                pipe.hincrby(trig_key, report.status.value, 1)
            await pipe.execute()
            return RecordResult.STORED
        except Exception as exc:  # noqa: BLE001 - 连不上时如实报失败，不装作成功
            logger.warning("回传写入失败: %s", exc)
            return RecordResult.UNAVAILABLE

    async def snapshot(self) -> Dict[str, Any]:
        """读不到就返回 available=false，不编造零值——道理与
        `stats.Stats.snapshot` 相同。

        `by_trigger` 按 `trigger_type → arm → status` 三层给，**这是 P0-1
        要补的那条**：原来 `by_trigger` 只按 `trigger_type` 存、混着两个
        arm 一起计数，写得进去但读不出这一局是干预组还是对照组——「哪类
        异动对干预最敏感」「总体差异是不是被某一类异动的构成比例带偏」
        这两句话本来就问不出来。现在键上带了 arm，比较才成立。
        空的组合（没人报过）直接不出现在字典里，不补零——道理与
        `stats.py` 的 `clue_buckets`／`trust_buckets` 不同：那两个是定长
        数组，下标本身有意义；这里的 key 是枚举组合，缺失就是没有数据，
        补一个 0 会跟"报过 0 次"混为一谈。
        """
        if not self.enabled:
            return {"available": False}
        combos = [(t, a) for t in TriggerType for a in Arm]
        try:
            conn = self._conn()
            pipe = conn.pipeline()
            pipe.hgetall(f"{_NS}:outcomes:{Arm.INTERVENTION.value}")
            pipe.hgetall(f"{_NS}:outcomes:{Arm.CONTROL.value}")
            for trigger, arm in combos:
                pipe.hgetall(f"{_NS}:outcomes:by_trigger:{trigger.value}:{arm.value}")
            results = await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("回传读取失败: %s", exc)
            return {"available": False}

        intervention, control, *by_trigger_raw = results
        by_trigger: Dict[str, Dict[str, Dict[str, int]]] = {}
        for (trigger, arm), raw in zip(combos, by_trigger_raw):
            counts = {k: int(v) for k, v in (raw or {}).items()}
            if counts:
                by_trigger.setdefault(trigger.value, {})[arm.value] = counts

        return {
            "available": True,
            "intervention": {k: int(v) for k, v in (intervention or {}).items()},
            "control": {k: int(v) for k, v in (control or {}).items()},
            "by_trigger": by_trigger,
        }


# 单例。与 `stats` / `guard` 同一个做法：连接惰性建立，
# 导入本模块不产生任何网络行为。
outcome_store = OutcomeStore(settings.redis_url)
