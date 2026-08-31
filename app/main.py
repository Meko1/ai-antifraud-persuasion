"""服务入口。

平台硬约束：对外 Web 服务固定监听 21818，`http://ip:21818/` 必须能直接打开。

承载：开局、一轮对局（SSE）、健康检查、全局统计、静态首页。
对局引擎在 `app/engine.py`，判分在 `app/scoring.py`，本文件只做 HTTP 层的事——
校验令牌、挡住重放、把事件流转成 SSE、顺手旁听统计。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import APP_ID, APP_VERSION
from .config import BASE_DIR, settings
from .engine import SessionFinished, play_turn
from .guard import breaker, guard, replay_key, turn_key
from .persona import opening_for
from .scenario import scenario_for
from .gateway import ModelGateway
from .llm import LLMError, llm_client
from .offline import OfflineGateway
from .provenance import versions
from .scoring import MAX_ROUNDS, WIN_THRESHOLD, is_finished, mood_for
from .state_token import (
    InvalidStateToken,
    TOKEN_TTL_SECONDS,
    Origin,
    new_session,
    sign_session,
    verify_token,
)
from .stats import stats
from .transcripts import disclosure, transcripts
from .trigger import TriggerError, build_context, catalog

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(APP_ID)

STATIC_DIR = BASE_DIR / "static"

# 启动时选定，运行期不自动切换（ADR-0005）。
#
# 离线演示模式在这里换掉整个网关（app/offline.py）：**同一个选择时刻、
# 同样的"选定就不再变"**，因此它与 ADR-0005 不冲突——那条决策否掉的是
# 运行期悄悄换供应商，不是启动期显式选一个。
_gateway: Any = OfflineGateway() if settings.offline_demo else ModelGateway()
if settings.offline_demo:
    logger.warning(
        "离线演示模式已开启：台词为预置内容，不调用大模型。判分照常（ADR-0001）。"
    )

app = FastAPI(
    title="AI 反诈劝阻",
    version=APP_VERSION,
    # 生产环境关闭 /docs 与 /openapi.json：对外暴露接口文档会被安全扫描告警
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
)


# ── 安全响应头 ─────────────────────────────────────────────────────────────
#
# 2026-08-18 补。大赛的 security-skill 评分里这一项扣了 6 分（未配 CSP、
# 无安全头中间件），而说明文档写着「所有投稿作品部署后，平台将联合安全部门
# 进行全面的安全漏洞扫描」——这类扫描器第一条查的就是响应头。
#
# **CSP 按这个作品实际加载的东西写，不抄模板。** 它只加载同源的一个 JS、
# 一个 CSS，没有 CDN、没有外链字体、没有图片外链、不嵌 iframe：
#   · script-src / style-src 只给 'self'，**不给 'unsafe-inline'**
#     （技能给的模板里有，那是为了兼容内联脚本；本作品没有内联脚本与内联样式，
#     给了反而白白放宽）
#   · 分享卡用 canvas 生成 PNG 塞进 <img>，所以 img-src 要 data: 和 blob:
#   · connect-src 只有同源（SSE 走 /api/game/turn）
#   · frame-ancestors 'none' 顶掉点击劫持，object-src 'none' 顶掉老插件面
#
# HSTS 没加：平台是 http://ip:21818 直连，没有 TLS，发 HSTS 只会让浏览器
# 把这个 host 记进强制 HTTPS 列表，反而打不开。有域名和证书之后再加。
#
# ── frame-ancestors：2026-08-25 从 'none' 改成可配置，默认值一点没变 ────────
#
# 起因是大赛展示页的实拍截图：作品详情页有「图集 / 演示视频 / **作品展示**」
# 三个页签，而《作品规范》写着「Mac 类或桌面端作品，需同时提供**可嵌入网页**
# 的 Web 展示方案」。这两条放在一起，几乎可以确定「作品展示」那一栏是把在线
# 链接嵌进 iframe。
#
# `frame-ancestors 'none'` + `X-Frame-Options: DENY` 会让那一栏变成一块空白，
# **而作者自己点在线链接是好的，多半到最后都不知道**。
#
# 处理方式是白名单，不是删掉：
#   · 不配 `FRAME_ANCESTORS` → 行为与改动前逐字节相同（'none' + DENY）
#   · 配了（空格分隔的 origin 列表）→ CSP 换成这份白名单，
#     并且**把 X-Frame-Options 整条撤掉**——它只认单一 origin，
#     留着会和 CSP 打架，而 CSP 的 frame-ancestors 在现代浏览器里优先级更高
#
# 换句话说：点击劫持的防线仍然在，只是从「谁都不许」收窄成「只许这一个」。
def build_security_headers(frame_ancestors: str = "") -> Dict[str, str]:
    """按 `frame_ancestors` 组一份响应头。

    做成纯函数是为了让两条分支都能被测到——读 `os.getenv` 的模块级常量
    只能测到进程启动时那一种，而这次改动的全部风险恰恰在另一种。
    """
    allow = (frame_ancestors or "").strip()
    csp = "; ".join((
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        f"frame-ancestors {allow}" if allow else "frame-ancestors 'none'",
    ))
    headers = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if not allow:
        headers["X-Frame-Options"] = "DENY"
    else:
        # CORP 也拦 iframe 导航。放开 frame-ancestors 却留着 `same-origin`
        # 的 CORP，等于修了一半——那一栏照样是空白，而排查成本比一开始
        # 就没改还高。
        headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    return headers


SECURITY_HEADERS = build_security_headers(os.getenv("FRAME_ANCESTORS", ""))
CSP = SECURITY_HEADERS["Content-Security-Policy"]


@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    # setdefault 语义：不覆盖某个响应自己已经设好的头
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.on_event("startup")
async def _on_startup() -> None:
    logger.info("%s v%s 启动完成，监听端口 %s", APP_ID, APP_VERSION, settings.port)
    # **配置摘要要在启动日志里**（P1-10 完成标准之一）。provider 与 protocol
    # 现在是闭集、拼错起不来，但"起来了之后到底在跟谁说话"仍然只有日志说得清。
    # `summary()` 不含 api_key。
    logger.info("大模型配置 %s", settings.llm.summary())
    logger.info("版本 %s", versions())
    logger.info(
        "重放与限流：共享存储=%s，开局 %s/分，出手 %s/分",
        guard.shared, settings.rate_limit_start, settings.rate_limit_turn,
    )


# ── 限流 ──────────────────────────────────────────────────────────────────
#
# 接口全部无鉴权（这个作品当前没有宿主 App 的可信身份可用），所以限流是
# 唯一那道闸。按来源 IP 分桶——它挡不住一个铁了心的人，但挡得住
# "一条 curl 循环把共享网关额度吃光"，而后者才是投票日真实会发生的事。
#
# 真实接入之后这里要换成宿主 App 的用户与设备维度，IP 那一层保留做兜底。
#
# 单例定义在 app/guard.py（测试要复位它，见 tests/conftest.py）。


def _client_key(request: Request) -> str:
    """限流的分桶键。

    优先取反代给的 `X-Forwarded-For` 第一跳——平台是 nginx 直连，
    不取的话所有人都会落进同一个桶，限流会变成"全站共用一个额度"。
    伪造它很容易，但伪造之后打散的是攻击者自己的桶，不影响正常用户。
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _too_many(scope: str) -> JSONResponse:
    return JSONResponse(
        {"code": "rate_limited", "message": "请求过于频繁，请稍后再试。"},
        status_code=429,
        headers={"Retry-After": "60"},
    )


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


@app.post("/api/game/start")
async def game_start(
    request: Request, body: Optional[StartRequest] = None
) -> JSONResponse:
    """开局。

    开场白取自预生成缓存，不调模型——首屏因此不受网关排队影响。

    **场景不再由随机 gid 派生**（P0-1）：它由开局上下文里的异动类型在服务端
    选定（app/trigger.py）。演示态下上下文是合成的，但走的是同一条选择逻辑。
    """
    if not await guard.allow(
        f"start:{_client_key(request)}", limit=settings.rate_limit_start
    ):
        return _too_many("start")

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
    # 开场白与人格变体同源：开场自称什么，后面十二轮就得是什么
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


# 玩家一句话的上限。前端输入框写了 maxlength="120"，但那只是前端——
# 一条 curl 就能把几十 KB 塞进提示词，直接吃掉共享的网关额度。
# 给到 200 是留出前端限制之外的余量，不是放宽玩法。
MAX_UTTERANCE_CHARS = 200

# 令牌上限。它随 history 增长（12 轮的发言与台词都在里面），实测满局约 7 KB。
MAX_TOKEN_CHARS = 32768


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


@app.post("/api/game/turn")
async def game_turn(
    request: Request,
    body: TurnRequest,
    gateway: ModelGateway = Depends(get_gateway),
) -> StreamingResponse:
    if not await guard.allow(
        f"turn:{_client_key(request)}", limit=settings.rate_limit_turn
    ):
        return _too_many("turn")
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
        yield _sse("error", {"code": "invalid_state"})
        return

    # 终局封口（引擎里也拦一道，见 engine.play_turn 顶部）。**在这里拦的好处是
    # 一个模型请求都不发**：拿终局令牌刷接口的人连网关额度都吃不到。
    if is_finished(session.state):
        logger.info("拒绝终局令牌：这一局已经结束")
        yield _sse("error", {"code": "finished"})
        return

    # 进门这一眼是**只读**的。整轮走完之后才记上（见本函数末尾的 claim）——
    # 网关抖一下导致这一轮失败时，玩家刚说的那句话必须还能再发一次。
    if await guard.seen(replay_key(body.token)):
        logger.info("拒绝重放令牌：这一轮已经打过了")
        yield _sse("error", {"code": "replayed"})
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
                if _src in _line_sources:
                    _line_sources[_src] += 1
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
            yield _sse(event.name, event.data)
        # 走完整轮才记消费。中途出错的那一张令牌必须还能重试——
        # 玩家刚说的那句话不该因为网关抖了一下就作废。
        await guard.claim(replay_key(body.token), ttl=TOKEN_TTL_SECONDS)
    except SessionFinished as exc:
        # 引擎那一层的终局封口。上面已经拦过一道，走到这里说明是**直接调用
        # play_turn 的路径**（或者两道之间有人改了判据）——如实报出来，
        # 不要落进 internal，那会把一个状态机约束伪装成服务端故障。
        logger.info("拒绝终局令牌（引擎层）: %s", exc)
        yield _sse("error", {"code": "finished"})
    except LLMError as exc:
        logger.warning("网关不可用: %s", exc)
        yield _sse("error", {"code": "upstream_unavailable"})
    except Exception:
        logger.exception("对局处理失败")
        yield _sse("error", {"code": "internal"})


def _sse(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── 存活与就绪是两件事 ────────────────────────────────────────────────────
#
# 原先只有一个 `/healthz`，而且**大模型没配、网关连不上的时候它照样返回
# `status: ok`**。于是一台完全无法完成任何一局对局的服务，在运维和负载均衡
# 眼里是健康的——故障期间的流量照常打进来，用户拿到的是一局全程降级的对话。
#
# 分成两个：
#
# · `/healthz` —— **存活**。进程还在、能响应 HTTP。start.sh 靠它判断服务
#   起来没有，所以它必须轻量、不依赖任何外部服务，也永远返回 200。
# · `/readyz`  —— **就绪**。这台服务现在能不能真的完成一局对局。
#   大模型没配、网关探测失败，一律返回 503。负载均衡该看的是这一个。
#
# 离线演示模式下 `/readyz` 是就绪的：它本来就不需要网关，而且这一点
# 在响应体里写着。


#: 台词是谁写的，逐轮累计。键与 `engine.py` 的 `line_source` 一一对应。
#
# **为什么要有这个计数**：`/healthz` 上原有的那几位回答的是"网关通不通"，
# 而那是一个**开局那一刻**的答案——探测成功之后网关照样可能每一轮都超时，
# 玩家拿到的每一句都是兜底台词，而 `/healthz` 一路绿。
#
# 兜底台词是**故意**写得让人察觉不出来的（`fallback.py` 顶部那句
# "玩家未必察觉：骗子本来就说车轱辘话"），所以它也骗得过运维。
# 在此之前唯一的痕迹是一行 `logger.warning`。这三个数把"这台服务到底
# 在用大模型，还是在发罐头"变成一个能一眼看完的比值。
#
# 进程内计数，不进 Redis：它回答的是"这个进程现在怎么样"，
# 重启归零正是想要的语义。
_line_sources: Dict[str, int] = {"model": 0, "fallback": 0, "absorbed": 0}


def _probe_allowed(request: Request) -> bool:
    """`?probe=1` 能不能用。

    探测会让服务**从公网请求触发一次对内网网关的出站调用**，并把网关地址、
    状态码回给调用方。这在拿到服务器当天是个必要的排查动作，
    但它不该是一个任何人都能打的公开接口。

    两条放行：本机来的（运维 ssh 上去 curl localhost），
    或者带着对得上的 `HEALTH_PROBE_TOKEN`（配了才有这条路）。
    """
    host = request.client.host if request.client else ""
    if host in {"127.0.0.1", "::1", "localhost"}:
        return True
    token = os.getenv("HEALTH_PROBE_TOKEN", "").strip()
    return bool(token) and request.headers.get("x-probe-token", "") == token


@app.get("/healthz")
async def healthz(request: Request, probe: int = 0) -> JSONResponse:
    """存活检查。**永远 200**，只要进程还在。

    带 `?probe=1` 时额外探测大模型网关连通性——**仅限本机或持令牌**，
    见 `_probe_allowed`。
    """
    body = {
        "status": "ok",
        "app": APP_ID,
        "version": APP_VERSION,
        "port": settings.port,
        "llm_provider": settings.llm.provider,
        "llm_configured": settings.llm.configured,
        # ADR-0007：这个进程是不是已经自动切到公网模型了。**这一位不能省**——
        # ADR-0005 反对自动切换的理由正是"无人知情"，自动切换本身没错，
        # 悄悄切才是问题。切没切、什么时候切的、原始报错是什么，都在这儿。
        "llm_failover": llm_client.status(),
        # 配置里写的那个模型（启动时定死）。**和上面 `llm_failover.active_model`
        # 一起看**：两者不一致就说明这个进程已经自动切到退路上去了。
        "llm_model": settings.llm.model,
        # 台词是谁写的。`fallback` 一直在涨 = 网关探测得通、但每一轮都在超时，
        # 玩家看到的是罐头。这一位是 `/healthz` 上唯一能反映**运行中**
        # 而不是**启动时**状态的东西。
        "line_sources": dict(_line_sources),
        # 离线演示模式必须在这里报出来。**一个看不出来是演示的演示是骗局**，
        # 而健康检查是运维唯一会看的那一处
        "offline_demo": settings.offline_demo,
        # 重放防护现在到底是共享的还是单进程的。多 worker 部署时这一位
        # 决定了防护是真的在生效，还是只在各自的进程里生效
        "replay_shared": guard.shared,
    }
    if probe:
        if not _probe_allowed(request):
            body["llm_probe"] = {"ok": False, "reason": "probe 仅限本机或持令牌调用"}
        else:
            body["llm_probe"] = await llm_client.probe()
            # ADR-0007 的退路也探一次。**探退路要趁还没用上它的时候**——
            # 真等到内网 token 用尽那天才发现公网凭证是错的，会切换"成功"
            # 之后立刻再失败，最后落回兜底台词，比根本没配还难查。
            # 没有 fallback 的部署这里是 None，那一栏就不出现
            fallback = await llm_client.probe_fallback()
            if fallback is not None:
                body["llm_probe_fallback"] = fallback
    return JSONResponse(body)


@app.get("/readyz")
async def readyz() -> JSONResponse:
    """就绪检查。**不能完成一局对局就返回 503。**

    判据只有一条：这一局能不能走完。离线演示模式下答案是"能"——
    它压根不调网关，而且判分是纯函数（ADR-0001），一格都不打折。
    """
    ready = settings.offline_demo or settings.llm.configured
    body = {
        "ready": ready,
        "app": APP_ID,
        "version": APP_VERSION,
        "offline_demo": settings.offline_demo,
        # **不含 api_key**（见 config.LLMSettings.summary）
        "llm": settings.llm.summary(),
        "versions": versions(),
        "reason": "" if ready else "大模型未配置，且未开启离线演示模式",
    }
    return JSONResponse(body, status_code=200 if ready else 503)


@app.get("/api/stats")
async def api_stats(sid: str = "", source: str = "") -> JSONResponse:
    """全局统计。Redis 是旁路，不可用时返回 available=false（§7.1）。

    `sid` 只影响信任度分布：复盘那句「高于同场景 X% 的已完成对局」要成立，
    比较的必须是同一个场景。不传就落到默认场景，与旧前端兼容。

    `source` 决定读哪个口径（演示态 / 真实接入）。同样是为了让那句话成立——
    比的必须是同一类对局，见 `stats.snapshot`。
    """
    return JSONResponse(await stats.snapshot(sid, source))


async def _demo_tokens() -> AsyncIterator[str]:
    """不依赖大模型的流式验证。

    用来证明 SSE 链路在真实部署环境（含平台反向代理）下没有被缓冲住 ——
    如果中间层做了响应缓冲，流式输出会退化成"憋很久然后一次吐完"，
    那是投票日体验崩掉的隐形杀手，必须在部署第一天就验证。
    """
    line = "别劝我。老师说了今天最后一天上车。车我已经卖了。"
    for ch in line:
        yield f"data: {ch}\n\n"
        await asyncio.sleep(0.05)
    yield "data: [DONE]\n\n"


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


@app.post("/api/game/exit")
async def game_exit(request: Request, body: ExitRequest) -> JSONResponse:
    """用户主动离开这次干预。

    **它不改变任何对局状态**，也不发新令牌：退出之后这一局就停在原地，
    用户回来还能续（前端 sessionStorage 里那一份还在）。
    这里只记一笔护栏指标。
    """
    if not await guard.allow(
        f"exit:{_client_key(request)}", limit=settings.rate_limit_turn
    ):
        return _too_many("exit")

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


@app.get("/api/demo/stream")
async def demo_stream(request: Request) -> StreamingResponse:
    if not await guard.allow(
        f"demo:{_client_key(request)}", limit=settings.rate_limit_turn
    ):
        # StreamingResponse 的签名要求这里也返回一个响应对象，
        # 429 用普通 JSON 回就行——调用方拿到的是明确的拒绝，不是一条空流
        return _too_many("demo")  # type: ignore[return-value]
    return StreamingResponse(
        _demo_tokens(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉 nginx 类反向代理的缓冲，否则 SSE 会被攒着一次性下发
            "X-Accel-Buffering": "no",
        },
    )


# 静态资源一律**必须回源校验**。
#
# 起因是一次真实的排查：部署之后首页是新的、复盘页还是旧的，看着像合并把
# 代码弄丢了，实际上代码好好的——`index.html` 走 FileResponse（没有缓存头，
# 浏览器每次都要），而 `/static/app.js` 由 StaticFiles 下发，**带 ETag 却不带
# Cache-Control**。缺了 Cache-Control 时浏览器按启发式规则自己决定能缓存多久
# （常见做法是拿 Last-Modified 的时间差乘 10%），这段时间内根本不回源问一句。
# 于是新 HTML 配旧 JS：首页是 index.html 里的静态结构，所以看着更新了；
# 复盘页整个由 app.js 生成，于是停在旧版。**同一次部署，两个文件新旧不一致。**
#
# `no-cache` 不是"不缓存"，是"可以缓存，但用之前必须回源校验一次"。
# ETag 还在，校验命中就是一个 304（空body），带宽几乎不花，但永远不会
# 拿旧文件糊到用户脸上。这比给文件名加版本号（app.js?v=xxx）简单：
# 那个要改 HTML、要有构建步骤，而这个作品没有构建步骤。
_NO_CACHE = "no-cache"


class RevalidatedStatic(StaticFiles):
    """带 no-cache 的静态目录。除此之外与 StaticFiles 完全一致。"""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = _NO_CACHE
        return response


@app.get("/")
async def index() -> FileResponse:
    # 首页本来就没有缓存头，这里显式写上——省得下一个人看见 /static 有、
    # 这里没有，以为是漏了。
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": _NO_CACHE}
    )


app.mount("/static", RevalidatedStatic(directory=STATIC_DIR), name="static")
