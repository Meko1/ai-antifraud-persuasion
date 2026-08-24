"""全局统计（旁路）。

Redis 在这里只做展示用的计数：总局数、各结局占比、各钥匙命中率（§10.2）。
它**不参与任何一局对局**——没配 REDIS_URL、没装 redis 包、连不上、写超时，
游戏都照常进行，`/api/stats` 老实返回 `available: false`。

做成旁路而不是依赖，是因为投票日当天最不能接受的故障模式，是一个纯展示
功能把对局拖垮。所以这里的每一次写都满足三条：
1. 不 await 在对局路径上（fire-and-forget，见 `_spawn`）；
2. 任何异常都吞掉，只留一条 debug 日志；
3. 连接惰性建立，导入本模块不产生任何网络行为。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, Optional, Set
from urllib.parse import urlsplit, urlunsplit

from .config import settings
from .provenance import DataMode
from .scoring import ALL_PENALTIES, KEY_VALUES, Ending

logger = logging.getLogger(__name__)

try:  # redis 是选填依赖，没装就等于没开这个功能
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - 取决于部署环境装没装
    Redis = None  # type: ignore[assignment]

# ── 键名前缀 ───────────────────────────────────────────────────────────────
#
# **2026-08-18 加的前缀，起因是大赛的 Redis 是共享实例、而且要求所有作品都用
# db0。** 原来的键叫 `stats:games`、`stats:turns`——这是任何一个参赛作品都会
# 随手取的名字。同一个 db0 里跑着几十个作品，两个用了同名键就会**互相把对方
# 的计数器加上去**，谁也看不出来，复盘里那句「别人打成什么样」会显示别人的数。
#
# 共享 db 上不许用通用键名，这一条与本作品无关，是任何人上共享 Redis 都该做的。
_NS = "ai-antifraud-persuasion"

KEY_GAMES = f"{_NS}:stats:games"
KEY_TURNS = f"{_NS}:stats:turns"
KEY_ENDINGS = f"{_NS}:stats:endings"
KEY_HITS = f"{_NS}:stats:hits"

# ── 业务口径与演示口径分开存 ───────────────────────────────────────────────
#
# **这是 P1-4 与 P1-13 的落点，也是这个模块 2026-08-23 唯一的结构性改动。**
#
# 在此之前所有事件写进同一组键：离线演示的关键词分类结果、分类超时那一轮的
# 空标签、真实模型判出来的命中，三者混在一个 `stats:hits` 里。于是
# "苏格拉底式提问命中率 12%" 这个数同时包含了
#   · 模型判的（想要的）
#   · 关键词规则判的（离线演示，反映的是规则表写得全不全）
#   · 根本没判的（网关超时，按中性记的空标签）
# 三种东西。拿它去比场景、去校准提示词、去训分类器，比不出任何东西。
#
# 现在按 `data_mode` 分命名空间：**默认口径只含 live 且未降级的那一份**，
# 演示与降级各自有键，需要时能查得到，但绝不会悄悄混进业务数字。
#
# 分组（arm）同样分开：干预组与对照组的对比是这次转向要算的第一个数，
# 而它必须从事件写下去的那一刻就分开，事后按什么字段都拆不出来。
def _ns(mode: str) -> str:
    return f"{_NS}:stats:{mode}"


def key_games(mode: str, arm: str = "") -> str:
    return f"{_ns(mode)}:games" + (f":{arm}" if arm else "")


def key_turns(mode: str) -> str:
    return f"{_ns(mode)}:turns"


def key_hits(mode: str) -> str:
    return f"{_ns(mode)}:hits"


def key_endings(mode: str, arm: str = "") -> str:
    return f"{_ns(mode)}:endings" + (f":{arm}" if arm else "")


def data_mode(*, offline: bool, degraded: bool = False, source: str = "") -> str:
    """这条事件记进哪个口径。

    三档，优先级从严到宽：

    · `degraded` —— 分类没跑成，标签是程序按中性填的。**它排在最前**，
      因为它既不是模型的判断，也不是规则的判断，混进任何一档都是脏数据。
    · `offline_demo` —— 离线演示，或者上下文是服务端合成的演示态。
    · `live` —— 真实模型 + 真实（或至少是上游传来的）上下文。

    `source == "demo"` 也落进 `offline_demo`：那条异动是合成的，
    这一局不对应任何真实的资产异动，业务口径里不该有它。
    """
    if degraded:
        return "degraded"
    if offline or source == "demo":
        return DataMode.OFFLINE_DEMO.value
    return DataMode.LIVE.value
# 信任度分布**按场景分开存**。
#
# 原先是一个全局哈希，四个场景混在一起算百分位。场景之间难度并不一样
# （`balance_sim` 实测 expert 胜率 45.8%~54.0%），混着比会让"你超过了多少人"
# 变成"你抽到的场景是难是易"。复盘页那句口径写的是「只比较相同客户、
# 相同规则版本的有效记录」，要让这句话是真的，键上就得带场景。
#
# 代价是样本攒得慢了四倍（门槛仍是每场景 20 局），这是对的取舍：
# 宁可不显示，也不显示一个不成立的排名。
def key_trust(sid: str, mode: str = DataMode.LIVE.value) -> str:
    # 口径同样进键名。混着算的话，"你超过了多少人"会把离线演示那批
    # 预置台词打出来的分数也算成"人"
    return f"{_ns(mode)}:trust:{sid or 'default'}"

# 只有走到阶梯里的四档才贡献一笔信任度分布；被拉黑「不入档」（CONTEXT.md
# 「结局」），信任度必然是 0，混进分布会把所有人的百分位都顶得虚高——
# 复盘里「本机训练记录」的最好成绩排名已经照这条排除过一次，这里是同一条原则。
_LADDER_KINDS = {"persuaded", "intercepted", "stalled", "transferred"}

# 5 分一档，20 个桶（0-4 … 95-100）。存分桶而不是每一局的原始信任度，
# 是为了让这份存储的大小是常数——不会跟着局数一直长，这点在大赛共享的
# Redis 实例上比较要紧。代价是百分位是"落在哪个区间"的近似值，不是精确排名。
_TRUST_BUCKETS = 20


def _trust_bucket(trust: int) -> str:
    t = max(0, min(100, trust))
    return str(min(_TRUST_BUCKETS - 1, t // 5))


def _rounds_bucket(rounds: int) -> str:
    """退出时打到第几轮。分桶而不是精确值，理由见 `record_exit`。"""
    if rounds <= 0:
        return "0"
    if rounds <= 3:
        return "1-3"
    if rounds <= 8:
        return "4-8"
    return "9+"

# Redis 卡住时不能把对局拖住，超时给得比正常往返大两个数量级也才半秒
_TIMEOUT = 0.5

# create_task 的返回值不留引用会被 GC 提前回收，任务就悄悄没了
_pending: Set["asyncio.Task[None]"] = set()


def force_db0(url: str) -> str:
    """把连接串的库号钉死在 0。

    大赛的共享 Redis **要求所有作品都用 db0**（原文三个感叹号）。
    `redis://host:port` 不写库号时 redis-py 默认就是 0，但"默认是 0"和
    "写死是 0"是两回事：谁手滑写成 `/1`，写进去的数据在看板上就永远查不到，
    而且不报错——这种错只会在"为什么统计是空的"上耗掉半天。

    所以这里不信任输入：URL 带了别的库号就改回 0，并留一行日志说明改过。
    不抛错——统计是旁路功能，任何情况下都不该把服务拦住（见模块顶部）。
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - 连拆都拆不开的串，交给 redis-py 报错
        return url

    # rediss:// 与 unix:// 的 path 语义不同，只处理 redis/rediss
    if parts.scheme not in ("redis", "rediss"):
        return url

    current = parts.path.lstrip("/")
    if current in ("", "0"):
        return urlunsplit(parts._replace(path="/0"))

    logger.warning(
        "REDIS_URL 指定了 db=%s，已按大赛要求改用 db=0", current
    )
    return urlunsplit(parts._replace(path="/0"))


class Stats:
    """计数器。所有方法在 Redis 不可用时都是安全的空操作。"""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Optional[Any] = None

    @property
    def enabled(self) -> bool:
        return bool(self._url) and Redis is not None

    def _conn(self) -> Any:
        if self._client is None:
            self._client = Redis.from_url(
                force_db0(self._url),
                socket_timeout=_TIMEOUT,
                socket_connect_timeout=_TIMEOUT,
                decode_responses=True,
            )
        return self._client

    # ── 写入（对局路径上调用，一律不等结果）──────────────────

    def record_start(
        self, *, arm: str = "", source: str = "", offline: Optional[bool] = None
    ) -> None:
        """干预曝光一次。

        **旧的全局 `stats:games` 仍然照写**，它是"这台服务一共被打开过几次"，
        看板上那个总数要用；新的按口径与分组分开的键是业务口径。
        两者不是同一个数，也不该合并成同一个。
        """
        if not self.enabled:
            return
        off = settings.offline_demo if offline is None else offline
        mode = data_mode(offline=off, source=source)
        _spawn(self._incr(KEY_GAMES))
        _spawn(self._incr(key_games(mode, arm)))

    def record_turn(
        self,
        hits: Iterable[str],
        *,
        degraded: bool = False,
        offline: Optional[bool] = None,
        arm: str = "",
        source: str = "",
    ) -> None:
        """一轮判分。turns 是命中率的分母，所以没命中也要记。

        **降级那一轮记进 `degraded` 口径，不进 live。** 它的 hits 是空的，
        在任何"命中率"里都长得和"玩家说了句没用的话"一模一样——
        网关抖一下不该记在人头上（前端 `scoredTurns()` 早就这么算了，
        后端一直没跟上）。
        """
        if not self.enabled:
            return
        off = settings.offline_demo if offline is None else offline
        mode = data_mode(offline=off, degraded=degraded, source=source)
        _spawn(self._turn(list(hits), mode))

    def record_ending(
        self, kind: str, *, offline: Optional[bool] = None,
        arm: str = "", source: str = "",
    ) -> None:
        if not self.enabled:
            return
        off = settings.offline_demo if offline is None else offline
        mode = data_mode(offline=off, source=source)
        _spawn(self._hincr(KEY_ENDINGS, kind))
        _spawn(self._hincr(key_endings(mode, arm), kind))

    def record_trust(
        self, kind: str, trust: int, sid: str = "",
        *, offline: Optional[bool] = None, source: str = "",
    ) -> None:
        """把最终信任度记一笔，供复盘算"超过同场景百分之多少的人"。

        只收四档正常结局；被拉黑必然是 0，见模块顶部 `_LADDER_KINDS` 的注。
        `sid` 决定记进哪个场景的分布，见 `key_trust`。
        """
        if not (self.enabled and kind in _LADDER_KINDS):
            return
        off = settings.offline_demo if offline is None else offline
        mode = data_mode(offline=off, source=source)
        _spawn(self._hincr(key_trust(sid, mode), _trust_bucket(trust)))

    def record_exit(
        self, reason: str, *, rounds: int = 0, arm: str = "", source: str = "",
        offline: Optional[bool] = None,
    ) -> None:
        """用户主动离开。**护栏指标，不是失败指标。**

        `rounds` 分桶记（0 / 1-3 / 4-8 / 9+），不记精确值：要回答的问题是
        "他们是在哪一段走的"，而精确到轮的分布在这个样本量下只是噪声。
        分桶还顺带避免了一件事——精确轮次 + 时间戳足以把一个人从统计里认出来。
        """
        if not self.enabled:
            return
        off = settings.offline_demo if offline is None else offline
        mode = data_mode(offline=off, source=source)
        field = f"{reason}:{_rounds_bucket(rounds)}" + (f":{arm}" if arm else "")
        _spawn(self._hincr(f"{_ns(mode)}:exits", field))

    async def _incr(self, key: str) -> None:
        try:
            await self._conn().incr(key)
        except Exception as exc:  # noqa: BLE001 - 旁路，绝不外抛
            logger.debug("统计写入失败（已忽略）: %s", exc)

    async def _hincr(self, key: str, field: str) -> None:
        try:
            await self._conn().hincrby(key, field, 1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("统计写入失败（已忽略）: %s", exc)

    async def _turn(self, hits: list, mode: str) -> None:
        try:
            pipe = self._conn().pipeline()
            pipe.incr(KEY_TURNS)
            pipe.incr(key_turns(mode))
            for name in hits:
                pipe.hincrby(KEY_HITS, name, 1)
                pipe.hincrby(key_hits(mode), name, 1)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.debug("统计写入失败（已忽略）: %s", exc)

    # ── 读取（只有 /api/stats 调用）────────────────────────

    async def snapshot(self, sid: str = "", source: str = "") -> Dict[str, Any]:
        """读不到就返回 available=false，不编造零值——
        「还没人玩过」和「统计挂了」是两件事，看板上不能混为一谈。

        `sid` 只影响信任度分布那一项：复盘要的是**同场景**的排名，
        其余几项（总局数、结局占比、钥匙命中率）仍然是全局口径。

        `source` 决定读哪个口径的信任度分布。复盘那句「高于同场景 X% 的
        已完成对局」要成立，比的必须是**同一类对局**——演示态的局跟演示态的比，
        真实接入的局跟真实接入的比。写进去时怎么分的（`data_mode`），
        读出来就怎么分，两边共用同一个函数，不会走散。
        """
        if not self.enabled:
            return {"available": False}
        mode = data_mode(offline=settings.offline_demo, source=source)
        try:
            conn = self._conn()
            pipe = conn.pipeline()
            pipe.get(KEY_GAMES)
            pipe.get(KEY_TURNS)
            pipe.hgetall(KEY_ENDINGS)
            pipe.hgetall(KEY_HITS)
            pipe.hgetall(key_trust(sid, mode))
            games, turns, endings, hits, trust_buckets = await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("统计读取失败: %s", exc)
            return {"available": False}

        games = int(games or 0)
        turns = int(turns or 0)
        endings = {k: int(v) for k, v in (endings or {}).items()}
        hits = {k: int(v) for k, v in (hits or {}).items()}
        finished = sum(endings.values())

        return {
            "available": True,
            "games": games,
            "turns": turns,
            "endings": {
                kind: {
                    "count": endings.get(kind, 0),
                    "share": _ratio(endings.get(kind, 0), finished),
                }
                # 枚举顺序即阶梯顺序，看板照着排就是对的；
                # 写死一份清单的话，加一档结局就会在这里悄悄漏掉
                for kind in (e.value for e in Ending)
            },
            # 分母是轮数而非局数：一局里同一把钥匙可以用很多次，
            # 除以局数会得出大于 1 的"命中率"
            "keys": {
                name: {"hits": hits.get(name, 0), "rate": _ratio(hits.get(name, 0), turns)}
                for name in (*KEY_VALUES, *ALL_PENALTIES)
            },
            # 20 个桶（每档 5 分），下标即 trust // 5。前端拿它自己算百分位——
            # 算法是复盘要展示的东西，不该埋进后端一个只吐一个数的接口里。
            # 桶内计数之和就是样本数，前端用它判断够不够门槛（复盘定的是 20 局）；
            # 不能直接拿 games 当样本数——games 含被拉黑、半途而废这些没入档的局。
            "trust_buckets": [
                int(trust_buckets.get(str(i), 0)) for i in range(_TRUST_BUCKETS)
            ],
            # 这份分布是哪个口径的。**看板上要能看见它**——
            # 一个不写明口径的百分位，读的人只能猜它跟谁比过
            "data_mode": mode,
        }


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


stats = Stats(settings.redis_url)
