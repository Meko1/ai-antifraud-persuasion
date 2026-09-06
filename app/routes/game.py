"""对局面：开局、一轮（SSE）、退出。

**这一层只做 HTTP 的事**——校验令牌、挡住重放、把引擎事件转成 SSE、
顺手旁听统计。编排在 `app/engine.py`，判分在 `app/scoring.py`。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, AsyncIterator, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import APP_ID
from ..config import settings
from ..engine import SessionFinished, play_turn
from ..gateway import ModelGateway
from ..guard import breaker, guard, replay_key, turn_key
from ..http import (
    MAX_TOKEN_CHARS,
    MAX_UTTERANCE_CHARS,
    client_key,
    line_sources,
    sse,
    too_many,
)
from ..llm import LLMError
from ..offline import OfflineGateway
from ..persona import opening_for
from ..scenario import scenario_for
from ..scoring import MAX_ROUNDS, WIN_THRESHOLD, is_finished, mood_for
from ..state_token import (
    InvalidStateToken,
    TOKEN_TTL_SECONDS,
    Origin,
    new_session,
    sign_session,
    verify_token,
)
from ..stats import stats
from ..transcripts import disclosure, transcripts
from ..trigger import TriggerError, build_context, catalog

logger = logging.getLogger(APP_ID)

router = APIRouter()


# 启动时选定，运行期不自动切换（ADR-0005）。
#
# 离线演示模式在这里换掉整个网关（app/offline.py）：**同一个选择时刻、
# 同样的"选定就不再变"**，因此它与 ADR-0005 不冲突——那条决策否掉的是
# 运行期悄悄换供应商，不是启动期显式选一个。
_gateway: Any = OfflineGateway() if settings.offline_demo else ModelGateway()


class StartRequest(BaseModel):
    """开局上下文（app/trigger.py）。

    **整个 body 是可选的**：大赛演示时没有任何上游系统在喂异动，
    不传就由服务端合成一条演示态的上下文，并在数据里自曝身份。
    """

    # 真实接入时由宿主 App 提供
    anomaly_id: str = Field(default="", max_length=128)
    trigger_type: str = Field(default="", max_length=64)
    transaction_ref: str = Field(default="", max_length=128)
    subject_ref: str = Field(default="", max_length=128)
    arm: str = Field(default="", max_length=32)
    triggered_at: Optional[int] = None
    # 演示态挑客户（P2「灵活与效率」）。真实接入时这一项被忽略——
    # 场景由用户自己那笔异动决定，见 trigger.build_context
    sid: str = Field(default="", max_length=32)


@router.post("/api/game/start")
async def game_start(
    request: Request, body: Optional[StartRequest] = None
) -> JSONResponse:
    """开局。

    开场白取自预生成缓存，不调模型——首屏因此不受网关排队影响。

    **场景不再由随机 gid 派生**（P0-1）：它由开局上下文里的异动类型在服务端
    选定（app/trigger.py）。演示态下上下文是合成的，但走的是同一条选择逻辑。
    """
    if not await guard.allow(
        f"start:{client_key(request)}", limit=settings.rate_limit_start
    ):
        return too_many("start")

    # 连续故障熔断（P1-13）。**已经在打的那一局照常降级走完**，
    # 但不再发放新的——网关整个挂掉时，每个新点进来的用户拿到的会是
    # 一局全程兜底台词、全程中性判分的对话，然后在复盘里读到一个由此
    # 推算出来的"结局"。那是把技术故障包装成了用户表现。
    #
    # 离线演示模式不受影响：它压根不调网关（判分是纯函数，ADR-0001）。
    if breaker.open and not settings.offline_demo:
        logger.warning("网关连续故障中，拒绝发放新干预")
        return JSONResponse(
            {
                "code": "unavailable",
                "message": "服务暂时不可用，请稍后再试。",
            },
            status_code=503,
            headers={"Retry-After": "30"},
        )

    try:
        ctx = build_context(body.model_dump() if body else None)
    except TriggerError as exc:
        logger.info("拒绝非法开局上下文: %s", exc)
        return JSONResponse({"code": "bad_context", "message": str(exc)}, status_code=400)

    gid = uuid.uuid4().hex
    scene = scenario_for(ctx.sid)
    # 开场白与人格变体同源：开场自称什么，后面每一轮就得是什么
    line = opening_for(gid, scene.personas)
    # 开场白必须进 session：它是第 1 轮唯一可供"扎根"的对话内容
    session = new_session(
        gid=gid,
        opening=line,
        sid=scene.id,
        origin=Origin(
            anomaly_id=ctx.anomaly_id,
            subject_ref=ctx.subject_ref,
            arm=ctx.arm.value,
            source=ctx.source,
            trigger_type=ctx.trigger_type.value,
        ),
    )
    # 干预曝光。**幂等键是 anomaly_id**：同一条异动重复弹出（用户刷新、
    # App 重启）只算一次曝光，否则完成率的分母会被刷新次数顶虚
    if await guard.claim(f"exposure:{ctx.anomaly_id}", ttl=TOKEN_TTL_SECONDS):
        stats.record_start(arm=ctx.arm.value, source=ctx.source)
    return JSONResponse(
        {
            "gid": session.gid,
            "opening": line,
            "remaining": MAX_ROUNDS,
            # 前端画那条细进度条与 80 线要用；判分参数只此一份，
            # 抄到前端去迟早对不上
            "trust": session.state.trust,
            # 对局中前端只显示情绪词，不显示分数。档位阈值同样只此一份——
            # 开局这一下没有 score 事件可用，所以在这里给出初始档位
            "mood": mood_for(session.state.trust).value,
            "win_threshold": WIN_THRESHOLD,
            # 这一局是被什么触发的、属于哪个分组、是不是演示态。
            # **前端要靠 source 决定说不说"这条异动是合成的"**——
            # 一个看不出来是演示的演示是骗局（与离线模式同一条原则）
            "origin": {
                "trigger_type": ctx.trigger_type.value,
                "arm": ctx.arm.value,
                "source": ctx.source,
            },
            # 可选客户清单（演示态才给）。真实接入时前端不该出现"换一位客户"
            "catalog": list(catalog()) if ctx.source == "demo" else [],
            # 开口之前那句告知（ADR-0006）。**由服务端下发，不写死在 index.html**：
            # 留存开着和关着说的不是同一句话，而"页面上写着不留存、服务端在留存"
            # 这种不一致，只可能以对用户说谎的形式出现。
            #
            # 离线演示时"数据去向"那一段整段换掉（不是追加一句），
            # 理由同源：**看不出来是演示的演示，就不是演示**，而自相矛盾的
            # 两句话比不说更糟。
            "notice": disclosure(),
            "offline_demo": settings.offline_demo,
            # 整个剧本的界面素材：客户档案、揭晓清单、金额、结局文案。
            # **前端不再写死任何一条**——写死的话，加场景时那些地方
            # 没有一处会提醒你漏改了（app/scenario.py 的 payload）
            "scenario": scene.payload(),
            "token": sign_session(
                session,
                secret=settings.state_signing_secret,
                issued_at=int(time.time()),
            ),
        }
    )


def get_gateway() -> ModelGateway:
    """网关的注入点。测试在这里换成替身，生产代码里没有任何 if TESTING。"""
    return _gateway



class TurnRequest(BaseModel):
    token: str = Field(max_length=MAX_TOKEN_CHARS)
    utterance: str = Field(max_length=MAX_UTTERANCE_CHARS)


# ── 重放防护与幂等 ────────────────────────────────────────────────────────
#
# 服务端不存会话（ADR-0003），令牌本身就是全部状态——于是玩家留着上一轮的
# 令牌重发，就能把说砸的那一轮撤销重来，两小时（TOKEN_TTL）内随便刷。
# 而本作唯一在判的东西是**时机**：能反悔，时机就不存在了。
#
# **两把锁管两件事，别混成一把**（见 app/guard.py 顶部那张表）：
#
# · `replay:<签名>` —— 玩家拿旧令牌撤销重来。只在一轮**整个走完**之后才记，
#   失败重试用的是一张从没被消费过的令牌，不受影响。
# · `turn:<gid>:<round>` —— 断流重试把同一轮的统计与语料写两遍。
#   它必须在 `score` 那一刻就认领，因为副作用就发生在那一刻。
#
# 原先只有前者，而且是单进程的 `OrderedDict`；后者根本不存在，于是
# 客户端在 `score` 之后、`done` 之前断开并重试，同一个 `(gid, round)`
# 会被写第二遍。现在两把锁都走共享存储（Redis 不可用时退回单进程，
# 局限写在 guard.py 里）。


@router.post("/api/game/turn")
async def game_turn(
    request: Request,
    body: TurnRequest,
    gateway: ModelGateway = Depends(get_gateway),
) -> StreamingResponse:
    if not await guard.allow(
        f"turn:{client_key(request)}", limit=settings.rate_limit_turn
    ):
        return too_many("turn")
    return StreamingResponse(
        _turn_events(body, gateway),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 少了它，反向代理会把流式攒成一次性下发，对话卡成弹窗
            "X-Accel-Buffering": "no",
        },
    )


async def _turn_events(body: TurnRequest, gateway: ModelGateway) -> AsyncIterator[str]:
    now = int(time.time())
    try:
        session = verify_token(
            body.token, secret=settings.state_signing_secret, now=now
        )
    except InvalidStateToken as exc:
        # 令牌不合法是预期情况之一（过期、伪造），不是服务端故障
        logger.info("拒绝非法令牌: %s", exc)
        yield sse("error", {"code": "invalid_state"})
        return

    # 终局封口（引擎里也拦一道，见 engine.play_turn 顶部）。**在这里拦的好处是
    # 一个模型请求都不发**：拿终局令牌刷接口的人连网关额度都吃不到。
    if is_finished(session.state):
        logger.info("拒绝终局令牌：这一局已经结束")
        yield sse("error", {"code": "finished"})
        return

    # 进门这一眼是**只读**的。整轮走完之后才记上（见本函数末尾的 claim）——
    # 网关抖一下导致这一轮失败时，玩家刚说的那句话必须还能再发一次。
    if await guard.seen(replay_key(body.token)):
        logger.info("拒绝重放令牌：这一轮已经打过了")
        yield sse("error", {"code": "replayed"})
        return

    # 台词只在 sentence 事件里出现，而留存要的是完整的一轮。事件顺序恒为
    # meta → sentence* → score → …（§7.1），所以攒到 score 那一刻就是全的。
    spoken: List[str] = []
    round_ = 0

    try:
        async for event in play_turn(
            session, body.utterance, gateway=gateway, secret=settings.state_signing_secret, now=now
        ):
            # 统计在这一层旁听，不塞进 engine：编排层不该知道 Redis 存在，
            # 而这里本来就是所有事件的必经之路
            if event.name == "meta":
                round_ = event.data.get("round", 0)
            elif event.name == "sentence":
                spoken.append(event.data.get("text", ""))
            if event.name == "score":
                # 台词是谁写的，累计一笔。**在幂等闸的外面**：它统计的是
                # "这个进程发出去过多少句罐头"，重试那一次同样发了一句，
                # 不该因为账已经记过就不算——它不是业务计数，是运行状态。
                _src = event.data.get("line_source", "model")
                if _src in line_sources:
                    line_sources[_src] += 1
                # **业务幂等键在这里认领**（P1-7）。副作用就发生在这一刻，
                # 所以锁也必须在这一刻上——客户端在 `score` 之后、`done` 之前
                # 断开并重试，同一个 (gid, round) 就不会被写第二遍。
                #
                # 认领失败不是错误：它意味着"这一轮的账已经记过了"，
                # 台词照常下发，只是不再重复记账。
                first_write = await guard.claim(
                    turn_key(session.gid, round_), ttl=TOKEN_TTL_SECONDS
                )
                if first_write:
                    stats.record_turn(
                        event.data.get("hits", ()),
                        # 降级轮次与离线演示都不进正式命中率（P1-4 / P1-13）：
                        # 技术故障和预置规则都不该被算成"用户表现"
                        degraded=event.data.get("degraded", False),
                        offline=settings.offline_demo,
                        arm=session.origin.arm,
                        source=session.origin.source,
                    )
                    # 对局语料（ADR-0006）。**脱敏在 transcripts 那一层做**，
                    # 这里一个字都不预处理——只有一个入口，就只有一处会漏。
                    transcripts.record_turn(
                        gid=session.gid,
                        sid=session.sid,
                        round_=round_,
                        utterance=body.utterance,
                        reply="".join(spoken),
                        hits=event.data.get("hits", ()),
                        grounded=event.data.get("grounded", False),
                        delta=event.data.get("delta", 0),
                        judged_mood=event.data.get("judged_mood", ""),
                        efficacy=event.data.get("efficacy"),
                        degraded=event.data.get("degraded", False),
                        evidence=event.data.get("evidence", ""),
                        origin=session.origin,
                    )
            elif event.name == "ending":
                kind = event.data.get("kind", "")
                # 终局同样要幂等：一局只有一个终局，键用 gid 就够
                if await guard.claim(f"ending:{session.gid}", ttl=TOKEN_TTL_SECONDS):
                    stats.record_ending(
                        kind, offline=settings.offline_demo,
                        arm=session.origin.arm, source=session.origin.source,
                    )
                    # sid 从令牌里的会话取，不从事件里取——事件不带场景，
                    # 而信任度分布是按场景分开存的（app/stats.py `key_trust`）
                    stats.record_trust(
                        kind, event.data.get("trust", 0), session.sid,
                        offline=settings.offline_demo, source=session.origin.source,
                    )
                    # 线索覆盖同理，按场景分桶。**引擎算好了才发**——
                    # 这个数不接受客户端上报（app/stats.py `record_clues`）
                    clues = event.data.get("clues") or {}
                    stats.record_clues(
                        int(clues.get("got", 0)), int(clues.get("of", 0)),
                        session.sid,
                        offline=settings.offline_demo, source=session.origin.source,
                    )
            yield sse(event.name, event.data)
        # 走完整轮才记消费。中途出错的那一张令牌必须还能重试——
        # 玩家刚说的那句话不该因为网关抖了一下就作废。
        await guard.claim(replay_key(body.token), ttl=TOKEN_TTL_SECONDS)
    except SessionFinished as exc:
        # 引擎那一层的终局封口。上面已经拦过一道，走到这里说明是**直接调用
        # play_turn 的路径**（或者两道之间有人改了判据）——如实报出来，
        # 不要落进 internal，那会把一个状态机约束伪装成服务端故障。
        logger.info("拒绝终局令牌（引擎层）: %s", exc)
        yield sse("error", {"code": "finished"})
    except LLMError as exc:
        logger.warning("网关不可用: %s", exc)
        yield sse("error", {"code": "upstream_unavailable"})
    except Exception:
        logger.exception("对局处理失败")
        yield sse("error", {"code": "internal"})



# ── 退出 ──────────────────────────────────────────────────────────────────
#
# **定位文档写着"不做成必须通过才能交易的障碍"，而在此之前聊天页只有输入框
# 和发送键**：没有关闭、没有稍后继续、没有返回交易、没有明确放弃。
# 在真实的交易前场景里，一个关不掉的弹窗换来的是抵触和投诉，而不是反思。
#
# 退出必须留痕，理由不是监控用户，是**护栏指标**：直接关闭率、中断率是
# 判断这个干预有没有伤害用户体验的第一组数。一个没人退出的干预和一个
# 所有人第一轮就退出的干预，在"完成率"这一个数上长得完全不一样，
# 而只有前者值得继续做。

class ExitRequest(BaseModel):
    token: str = Field(max_length=MAX_TOKEN_CHARS)
    # 用户是怎么走的。闭集，前端只发这几个值之一
    #   dismissed  —— 一轮都没打就关掉了（"直接关闭率"）
    #   abandoned  —— 打了几轮之后中途退出
    #   finished_early —— 主动结束并去看复盘（他做完了他想做的）
    reason: str = Field(default="abandoned", max_length=32)


_EXIT_REASONS = frozenset({"dismissed", "abandoned", "finished_early"})


@router.post("/api/game/exit")
async def game_exit(request: Request, body: ExitRequest) -> JSONResponse:
    """用户主动离开这次干预。

    **它不改变任何对局状态**，也不发新令牌：退出之后这一局就停在原地，
    用户回来还能续（前端 sessionStorage 里那一份还在）。
    这里只记一笔护栏指标。
    """
    if not await guard.allow(
        f"exit:{client_key(request)}", limit=settings.rate_limit_turn
    ):
        return too_many("exit")

    now = int(time.time())
    try:
        session = verify_token(
            body.token, secret=settings.state_signing_secret, now=now
        )
    except InvalidStateToken:
        # 退出这件事不值得因为一张过期令牌就报错给用户看。如实记不下来，
        # 但用户该走还是能走——**绝不能让埋点挡住退出**
        return JSONResponse({"ok": True, "recorded": False})

    reason = body.reason if body.reason in _EXIT_REASONS else "abandoned"
    # 一局只记一次退出。用户可能连点两下，或者退出后回来又退出
    recorded = await guard.claim(f"exit:{session.gid}", ttl=TOKEN_TTL_SECONDS)
    if recorded:
        stats.record_exit(
            reason,
            rounds=session.state.round,
            arm=session.origin.arm,
            source=session.origin.source,
        )
    return JSONResponse({"ok": True, "recorded": recorded})
