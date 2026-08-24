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
from .provenance import provenance
from .redact import redact

logger = logging.getLogger(__name__)

try:  # 与 stats 共用同一条依赖判断：选填，没装就等于没开这个功能
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - 取决于部署环境装没装
    Redis = None  # type: ignore[assignment]

_NS = "ai-antifraud-persuasion"

# ── 逐条留存，不是整表一个 TTL ─────────────────────────────────────────────
#
# **原来这里是一个 List + 每次写入重置整表 TTL，那个"最多 90 天"的承诺不成立。**
#
# 原实现每写一条就 `EXPIRE KEY 90天`，注释还写着这是刻意的——"留存期限量的是
# 最后一次有人在玩"。问题是**界面上对用户说的不是这句话**，界面说的是
# 「你与虚构客户的对话会在脱敏后留存最多 90 天」。一个持续有人使用的库里，
# 第 1 天写进去的那条记录会跟着第 89 天的写入一起续期，实际留存上限
# 由 20000 条容量决定，而不是每条记录自己的 90 天年龄。
#
# 用户告知与实际处理不一致，这一条比"存久了"本身严重：它是在对用户说谎。
#
# 换成 Sorted Set，**score 就是这条记录的到期时间戳**：
#   · 写入：ZADD key <到期时间> <json>
#   · 清理：ZREMRANGEBYSCORE key -inf <现在>  —— 按记录自己的年龄删
#   · 容量：ZREMRANGEBYRANK key 0 -(MAX+1)   —— 到顶丢最旧的
# 三条命令一个 pipeline 发完，代价与原来的 LPUSH+LTRIM+EXPIRE 完全一样。
#
# 备份、导出副本仍在这套策略之外——**那是流程问题，代码兜不住**，
# 写在 ADR-0006 里，不假装这里解决了。
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
#
# ## 2026-08-23 改了末尾那半句，因为原来那句是假的
#
# 原文是「账号、持仓与身份信息**不会被记录**」。这是一句关于**结果**的承诺，
# 而实际能力只是一层正则（app/redact.py）。实测这些仍会原样留下：
# 带空格的手机号、带空格的卡号、地址、工作单位、持仓名称、
# 以及自由文本里的一切身份与健康信息。
#
# 正则漏项不可能靠继续加规则彻底解决——中文姓名、住址没有可靠的形状。
# 所以改的不是脱敏，是**这句话本身**：承诺从"结果"退回"我们做了什么"，
# 并且如实说清哪一半靠的是"根本没往那儿存"。
#
# 这不是把标准降低，是把话说准。一句做不到的承诺，在真实 C 端接入之后
# 就是一份监管材料。
DISCLOSURE_RETENTION = (
    "你与虚构客户的对话会在脱敏后留存最多 {days} 天，仅用于改进本产品的判定模型。"
    "我们不会主动记录你的账号、持仓与设备信息；"
    "你自己打字输入的内容会经过自动去标识处理后留存，请不要在对话里填写真实的"
    "身份证号、银行卡号、住址或联系方式。"
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
        evidence: str = "",
        origin: Any = None,
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
            evidence=evidence,
            origin=origin,
        )
        _spawn(self._append(entry))

    async def _append(self, entry: Dict[str, Any]) -> None:
        try:
            now = int(time.time())
            pipe = self._conn().pipeline()
            # score = 这条记录自己的到期时间。**逐条**，不是整表一个 TTL
            pipe.zadd(
                KEY_TRANSCRIPTS,
                {json.dumps(entry, ensure_ascii=False): now + _TTL_SECONDS},
            )
            # 到期的删掉。清理挂在写路径上而不是定时任务里，理由是这个作品
            # 没有常驻调度器；代价是"没人玩的时候不清理"，而那种情况下
            # 也没有新数据在产生——最坏是过期记录多留到下一次有人开局
            pipe.zremrangebyscore(KEY_TRANSCRIPTS, "-inf", now)
            # 容量到顶丢最旧的（score 最小＝最早到期＝最早写入）
            pipe.zremrangebyrank(KEY_TRANSCRIPTS, 0, -(MAX_ENTRIES + 1))
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - 旁路，绝不外抛
            logger.debug("对局留存写入失败（已忽略）: %s", exc)

    async def purge(self, now: Optional[int] = None) -> int:
        """删掉已经到期的记录，返回删了几条。

        写路径上每次都会顺手清一遍，这个方法是给运维和审计用的：
        **"某条数据什么时候被删的"要答得出来**，就得有一个能主动调、
        能留下返回值的入口，而不是只有一个副作用。
        """
        if not self.enabled:
            return 0
        try:
            return int(
                await self._conn().zremrangebyscore(
                    KEY_TRANSCRIPTS, "-inf", now or int(time.time())
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("过期清理失败: %s", exc)
            return 0


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
    evidence: str = "",
    origin: Any = None,
) -> Dict[str, Any]:
    """拼一条记录。**纯函数，脱敏在这里发生**，因此测试不需要 Redis。

    字段就这些，一个都不多。**没有的东西比有的东西更要紧**：
    没有账号、没有持仓、没有设备指纹、没有 IP、没有任何来自异动信号那一侧的
    **内容**。转向之后触发这段对话的是一条真实的资产异动，把那条异动的详情
    存进来是最自然不过的下一步，也正是 ADR-0006 明确划在范围外的那一步。

    2026-08-23 加进来的是**关联键，不是内容**：`anomaly_id` / `arm` /
    `trigger_type` 三个标识符。没有它们，这批语料回答不了"干预组比对照组
    好在哪儿"；有了它们也仍然不含那笔交易的任何细节（金额、对手方、标的）。
    `transaction_ref` 刻意留在服务端事件表里，一个字都不进语料。

    `gid` 是**每局随机生成**的对局号（签名令牌里那一个），不是用户标识：
    同一个人玩两局是两个 gid，跨局关联不起来。留着它只为把同一局的十二轮
    串成一段对话——语料的价值在整段对话上，逐轮拆散就没法标了。

    ## 三段元数据，缺一段这批语料就不能用

    · `provenance` —— 谁给的标签、哪种模式、哪一版规则（app/provenance.py）。
      少了它，下一个人会拿模型自己的预测去训模型自己。
    · `origin`     —— 哪条异动、哪个实验组。少了它，算不出干预效果。
    · `idem`       —— 幂等键。同一个 (gid, round) 只该有一条记录，
      重复写入在离线清洗时要能一眼认出来并去重。
    """
    off = settings.offline_demo
    src = getattr(origin, "source", "") if origin is not None else ""
    return {
        # v2：加了 provenance / origin / idem 三段。**版本号必须跟着涨**，
        # 否则离线清洗脚本没法分辨一条记录该按哪套字段解析
        "v": 2,
        "gid": gid,
        "sid": sid,
        "round": round_,
        # 幂等键。**和服务端那把锁用同一个口径**（app/guard.py `turn_key`）
        "idem": f"{gid}:{round_}",
        "utterance": redact(utterance),
        "reply": redact(reply),
        "hits": sorted(set(hits)),
        "grounded": bool(grounded),
        "delta": int(delta),
        "judged_mood": judged_mood,
        "efficacy": efficacy,
        "degraded": bool(degraded),
        # 模型说它扎根在哪一句上。**给标注人员看的**：复核这条 grounded
        # 标签对不对时，"模型认为它引用了哪一句"是最快的线索。
        # 它一样要过脱敏——玩家的话会被模型原样抄进这个字段
        "evidence": redact(evidence),
        "ts": int(time.time()),
        # 到期时间也写进记录本身。Redis 那边的 score 是同一个数——
        # 两份是为了让导出的 JSONL 离开 Redis 之后**自己带着到期时间**，
        # 否则一份导出文件就成了一份没有留存期限的副本
        "expires_at": int(time.time()) + _TTL_SECONDS,
        **provenance(offline=off or src == "demo", degraded=bool(degraded)),
        "origin": {
            "anomaly_id": getattr(origin, "anomaly_id", ""),
            "arm": getattr(origin, "arm", ""),
            "trigger_type": getattr(origin, "trigger_type", ""),
            "source": src,
        } if origin is not None else {},
    }


def _spawn(coro: Any) -> None:
    import asyncio

    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


transcripts = TranscriptStore(settings.redis_url, settings.transcript_retention)
