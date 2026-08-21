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
import time
import uuid
from collections import OrderedDict
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import APP_ID, APP_VERSION
from .config import BASE_DIR, settings
from .engine import play_turn
from .persona import opening_for
from .scenario import pick_scenario, scenario_for
from .gateway import ModelGateway
from .llm import LLMError, llm_client
from .scoring import MAX_ROUNDS, WIN_THRESHOLD, mood_for
from .state_token import InvalidStateToken, new_session, sign_session, verify_token
from .stats import stats

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(APP_ID)

STATIC_DIR = BASE_DIR / "static"

# 启动时选定，运行期不自动切换（ADR-0005）
_gateway = ModelGateway()

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
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


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
    logger.info("大模型 provider=%s configured=%s", settings.llm.provider, settings.llm.configured)


@app.post("/api/game/start")
async def game_start() -> JSONResponse:
    """开局。

    开场白取自预生成缓存，不调模型——首屏因此不受网关排队影响。
    """
    # 场景与人格变体同一个做法：由 gid 哈希派生，服务端不存任何东西。
    # 派生出来的 sid 进令牌（v3），否则第 2 轮会被当成另一个场景重新算
    gid = uuid.uuid4().hex
    scene = pick_scenario(gid)
    # 开场白与人格变体同源：开场自称什么，后面十二轮就得是什么
    line = opening_for(gid, scene.personas)
    # 开场白必须进 session：它是第 1 轮唯一可供"扎根"的对话内容
    session = new_session(gid=gid, opening=line, sid=scene.id)
    stats.record_start()
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
            "contest_id": settings.contest_id,
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


# ── 重放防护 ──────────────────────────────────────────────────────────────
#
# 服务端不存会话（ADR-0003），令牌本身就是全部状态——于是玩家留着上一轮的
# 令牌重发，就能把说砸的那一轮撤销重来，两小时（TOKEN_TTL）内随便刷。
# 而本作唯一在判的东西是**时机**：能反悔，时机就不存在了。
#
# **只在一轮成功走完之后才记**，这一条是关键：失败重试用的是一张从没被消费过
# 的令牌，不受影响；被挡下的只有已经打完的那张。
#
# 局限写在明处：单进程内存，多 worker 或重启后失效。它挡的是"顺手撤销"，
# 不是"铁了心作弊"——后者要挡就得推翻 ADR-0003，不值这个价。
_CONSUMED_LIMIT = 4096
_consumed: "OrderedDict[str, None]" = OrderedDict()


def _fingerprint(token: str) -> str:
    """令牌的签名部分。它已经是 payload 的 HMAC，够做唯一标识，也不必存正文。"""
    return token.rpartition(".")[2]


def _already_played(token: str) -> bool:
    return _fingerprint(token) in _consumed


def _mark_played(token: str) -> None:
    _consumed[_fingerprint(token)] = None
    while len(_consumed) > _CONSUMED_LIMIT:
        _consumed.popitem(last=False)


@app.post("/api/game/turn")
async def game_turn(
    body: TurnRequest, gateway: ModelGateway = Depends(get_gateway)
) -> StreamingResponse:
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

    if _already_played(body.token):
        logger.info("拒绝重放令牌：这一轮已经打过了")
        yield _sse("error", {"code": "replayed"})
        return

    try:
        async for event in play_turn(
            session, body.utterance, gateway=gateway, secret=settings.state_signing_secret, now=now
        ):
            # 统计在这一层旁听，不塞进 engine：编排层不该知道 Redis 存在，
            # 而这里本来就是所有事件的必经之路
            if event.name == "score":
                stats.record_turn(event.data.get("hits", ()))
            elif event.name == "ending":
                kind = event.data.get("kind", "")
                stats.record_ending(kind)
                # sid 从令牌里的会话取，不从事件里取——事件不带场景，
                # 而信任度分布是按场景分开存的（app/stats.py `key_trust`）
                stats.record_trust(kind, event.data.get("trust", 0), session.sid)
            yield _sse(event.name, event.data)
        # 走完整轮才记消费。中途出错的那一张令牌必须还能重试——
        # 玩家刚说的那句话不该因为网关抖了一下就作废。
        _mark_played(body.token)
    except LLMError as exc:
        logger.warning("网关不可用: %s", exc)
        yield _sse("error", {"code": "upstream_unavailable"})
    except Exception:
        logger.exception("对局处理失败")
        yield _sse("error", {"code": "internal"})


def _sse(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/healthz")
async def healthz(probe: int = 0) -> JSONResponse:
    """健康检查。

    start.sh 靠它判断服务是否真的起来了，因此必须轻量、不依赖外部服务。
    带 ?probe=1 时才额外探测大模型网关连通性 —— 这是拿到服务器当天
    用来确认独立网段能否访问内网网关的那一下。
    """
    body = {
        "status": "ok",
        "app": APP_ID,
        "version": APP_VERSION,
        "port": settings.port,
        "llm_provider": settings.llm.provider,
        "llm_configured": settings.llm.configured,
    }
    if probe:
        body["llm_probe"] = await llm_client.probe()
    return JSONResponse(body)


@app.get("/api/stats")
async def api_stats(sid: str = "") -> JSONResponse:
    """全局统计。Redis 是旁路，不可用时返回 available=false（§7.1）。

    `sid` 只影响信任度分布：复盘那句「高于同场景 X% 的已完成对局」要成立，
    比较的必须是同一个场景。不传就落到默认场景，与旧前端兼容。
    """
    return JSONResponse(await stats.snapshot(sid))


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


@app.get("/api/demo/stream")
async def demo_stream() -> StreamingResponse:
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
