"""对局留存（旁路）。

**这个模块推翻了 ADR-0003 的一半**，所以先说清楚推翻了哪一半：
对局**状态**仍然由客户端持有并签名，服务端照旧不存会话、重启不丢局；
新增的只是一份**只写不读的语料**，任何一局对局都不依赖它。
例外范围、为什么是现在、以及两条硬要求，全部写在
[ADR-0006](../docs/adr/0006-transcript-retention-for-the-pivot.md)。

写入纪律与 `app/stats.py` 完全一致，理由也一样——投票日最不能接受的故障，
是一个不参与对局的旁路功能把对局拖垮：

1. 不 await 在对局路径上（fire-and-forget）；
2. 任何异常都吞掉，只留一条 debug 日志；
3. 没配 Redis、没装 redis 包、连不上，都只是**不留存**，游戏照常。

多一条 stats 没有的：**默认关闭**。`TRANSCRIPT_RETENTION` 不显式打开就一条不存。
留存这件事不该由"忘了配环境变量"来决定方向——默认关，是让"开始存了"
必须是一个有人按下去的动作。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Iterable, Optional, Set

from .config import settings
from .redact import redact

logger = logging.getLogger(__name__)

try:  # 与 stats 共用同一条依赖判断：选填，没装就等于没开这个功能
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - 取决于部署环境装没装
    Redis = None  # type: ignore[assignment]

_NS = "ai-antifraud-persuasion"
KEY_TRANSCRIPTS = f"{_NS}:transcripts"

# 条数上限。共享 Redis 实例上不能无限长——这是别人的机器。
# 到顶之后丢最旧的：新语料比旧语料值钱，分类器要跟着提示词一起迭代。
MAX_ENTRIES = 20000

# 留存期限。**这个数字要和界面上告知用户的那一句一致**，改一处就要改两处，
# 所以它在代码里只有这一份，界面那句由 §3.1 的告知文案引用它。
RETENTION_DAYS = 90
_TTL_SECONDS = RETENTION_DAYS * 24 * 3600

_TIMEOUT = 0.5

_pending: Set[Any] = set()


# ── 告知 ──────────────────────────────────────────────────────────────────
#
# ADR-0006 的第二条硬要求：**在用户开口之前明确告知留存与用途**。
# "之前"是字面意思——这句话随 `/api/game/start` 一起下发，落在聊天窗口
# 第一条消息的上方，而不是藏在某个「隐私政策」链接后面。
#
# **它是一个函数不是一段静态文案**，理由只有一条：留存开着和关着说的不是
# 同一句话。写成两份文案，必然有一天只改了一份，而改错的那一份是在
# 对用户说谎。开关与措辞由 tests/test_transcripts.py 钉在一起。
# **不写"本练习"**：转向 C 端之后这不是一次练习，是用户按下确认之前的一次干预
# （POSITIONING「一句话」）。他没打算来练什么，也不该被暗示"你在受训"。
DISCLOSURE_FICTION = "对方是虚构客户，机构与人物均为虚构。本内容不构成投资建议；"

# 数据去向那一句**随离线模式换**，不是在后面补一句。
# 原先是拼接，于是离线演示时页面上先说"你输入的内容会发送至大模型"、
# 再说"不调用大模型"——两句话自相矛盾，而用户只会记住错的那一句。
DISCLOSURE_LLM = "你输入的内容会发送至大模型用于生成回复。"

DISCLOSURE_BASE = DISCLOSURE_FICTION + DISCLOSURE_LLM

# 三件事都要说到，缺一条这句话就不算告知：**存什么、存多久、拿来干什么**。
# 末尾那半句同样重要——用户最想知道的往往不是"你存了什么"，
# 而是"你有没有存我的账户"。
DISCLOSURE_RETENTION = (
    "你与虚构客户的对话会在脱敏后留存最多 {days} 天，仅用于改进本产品的判定模型；"
    "账号、持仓与身份信息不会被记录。"
)


def disclosure(
    enabled: Optional[bool] = None, offline: Optional[bool] = None
) -> str:
    """用户开口之前要看到的那句话。两个参数只给测试用，生产走配置。

    三段：虚构与非建议 → 数据去向 → 留存（开了才有）。
    **数据去向那一段是替换不是追加**，见 `DISCLOSURE_LLM` 上面那条注。
    """
    on = transcripts.enabled if enabled is None else enabled
    off_ = settings.offline_demo if offline is None else offline

    text = DISCLOSURE_FICTION
    if off_:
        from .offline import OFFLINE_NOTE

        text += OFFLINE_NOTE
    else:
        text += DISCLOSURE_LLM
    if on:
        text += DISCLOSURE_RETENTION.format(days=RETENTION_DAYS)
    return text


class TranscriptStore:
    """一局一局往里写。Redis 不可用或功能没开时，所有方法都是空操作。"""

    def __init__(self, url: str, enabled_flag: bool) -> None:
        self._url = url
        self._flag = enabled_flag
        self._client: Optional[Any] = None

    @property
    def enabled(self) -> bool:
        return bool(self._flag and self._url) and Redis is not None

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

    def record_turn(
        self,
        *,
        gid: str,
        sid: str,
        round_: int,
        utterance: str,
        reply: str,
        hits: Iterable[str],
        grounded: bool,
        delta: int,
        judged_mood: str,
        efficacy: Optional[float],
        degraded: bool,
    ) -> None:
        """记一轮。**脱敏在这里做，不在调用方做**——只有一个入口，就只有一处会漏。"""
        if not self.enabled:
            return
        entry = build_entry(
            gid=gid,
            sid=sid,
            round_=round_,
            utterance=utterance,
            reply=reply,
            hits=hits,
            grounded=grounded,
            delta=delta,
            judged_mood=judged_mood,
            efficacy=efficacy,
            degraded=degraded,
        )
        _spawn(self._append(entry))

    async def _append(self, entry: Dict[str, Any]) -> None:
        try:
            pipe = self._conn().pipeline()
            pipe.lpush(KEY_TRANSCRIPTS, json.dumps(entry, ensure_ascii=False))
            pipe.ltrim(KEY_TRANSCRIPTS, 0, MAX_ENTRIES - 1)
            # 每次写都把 TTL 顶回去。**这是刻意的**：留存期限量的是"最后一次
            # 有人在玩"，不是"第一条记录写进来的时间"。按后者算，一个持续在用的
            # 库会在第 90 天整个消失。
            pipe.expire(KEY_TRANSCRIPTS, _TTL_SECONDS)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - 旁路，绝不外抛
            logger.debug("对局留存写入失败（已忽略）: %s", exc)


def build_entry(
    *,
    gid: str,
    sid: str,
    round_: int,
    utterance: str,
    reply: str,
    hits: Iterable[str],
    grounded: bool,
    delta: int,
    judged_mood: str,
    efficacy: Optional[float],
    degraded: bool,
) -> Dict[str, Any]:
    """拼一条记录。**纯函数，脱敏在这里发生**，因此测试不需要 Redis。

    字段就这些，一个都不多。**没有的东西比有的东西更要紧**：
    没有账号、没有持仓、没有设备指纹、没有 IP、没有任何来自异动信号那一侧的
    数据。转向之后触发这段对话的是一条真实的资产异动，把那条异动的内容也
    存进来是最自然不过的下一步，也正是 ADR-0006 明确划在范围外的那一步。

    `gid` 是**每局随机生成**的对局号（签名令牌里那一个），不是用户标识：
    同一个人玩两局是两个 gid，跨局关联不起来。留着它只为把同一局的十二轮
    串成一段对话——语料的价值在整段对话上，逐轮拆散就没法标了。
    """
    return {
        "v": 1,
        "gid": gid,
        "sid": sid,
        "round": round_,
        "utterance": redact(utterance),
        "reply": redact(reply),
        "hits": sorted(hits),
        "grounded": bool(grounded),
        "delta": int(delta),
        "judged_mood": judged_mood,
        "efficacy": efficacy,
        "degraded": bool(degraded),
        "ts": int(time.time()),
    }


def _spawn(coro: Any) -> None:
    import asyncio

    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


transcripts = TranscriptStore(settings.redis_url, settings.transcript_retention)
