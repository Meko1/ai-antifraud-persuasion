"""运维面：存活、就绪、全局统计、不调模型的流式自检。

这一组接口一条都不碰对局状态。它们回答的是运维和负载均衡的问题——
**这台服务现在还在吗、能不能完成一局、它到底在用大模型还是在发罐头。**
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import APP_ID, APP_VERSION
from ..config import settings
from ..guard import breaker, guard
from ..http import client_key, line_sources, too_many
from ..llm import llm_client
from ..outcome import outcome_store
from ..provenance import versions
from ..stats import stats

logger = logging.getLogger(APP_ID)

router = APIRouter()


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


@router.get("/healthz")
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
        "line_sources": dict(line_sources),
        # 离线演示模式必须在这里报出来。**一个看不出来是演示的演示是骗局**，
        # 而健康检查是运维唯一会看的那一处
        "offline_demo": settings.offline_demo,
        # 重放防护现在到底是共享的还是单进程的。多 worker 部署时这一位
        # 决定了防护是真的在生效，还是只在各自的进程里生效
        "replay_shared": guard.shared,
        # **熔断张着的时候，这台服务对新用户就是坏的，而 `status` 仍然是 ok。**
        # 在此之前这件事在 /healthz 上一个字都看不到：用户那边写着"暂时无法
        # 接入客户"，运维这边一路绿，只能从"没人进得来"倒推。
        # 窗口内的失败数与样本数一并报出来——它回答的是"网关是挂了还是忙"。
        "breaker": breaker.snapshot(),
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


@router.get("/readyz")
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


@router.get("/api/stats")
async def api_stats(sid: str = "", source: str = "") -> JSONResponse:
    """全局统计。Redis 是旁路，不可用时返回 available=false（§7.1）。

    `sid` 只影响信任度分布：复盘那句「高于同场景 X% 的已完成对局」要成立，
    比较的必须是同一个场景。不传就落到默认场景，与旧前端兼容。

    `source` 决定读哪个口径（演示态 / 真实接入）。同样是为了让那句话成立——
    比的必须是同一类对局，见 `stats.snapshot`。

    `outcomes` 是 24 小时后回传的结果（app/outcome.py），按 arm 分组，
    `by_trigger` 再按 `trigger_type × arm` 分一层；与其余字段是两个独立的
    数据源，**没有回传就是 `available: false`**，不代表对局统计本身出了问题。
    """
    body = await stats.snapshot(sid, source)
    body["outcomes"] = await outcome_store.snapshot()
    return JSONResponse(body)


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


@router.get("/api/demo/stream")
async def demo_stream(request: Request) -> StreamingResponse:
    if not await guard.allow(
        f"demo:{client_key(request)}", limit=settings.rate_limit_turn
    ):
        # StreamingResponse 的签名要求这里也返回一个响应对象，
        # 429 用普通 JSON 回就行——调用方拿到的是明确的拒绝，不是一条空流
        return too_many("demo")  # type: ignore[return-value]
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
