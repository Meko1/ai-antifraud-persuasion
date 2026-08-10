"""服务入口。

平台硬约束：对外 Web 服务固定监听 21818，`http://ip:21818/` 必须能直接打开。
本文件目前只承载"部署链路跑通"所需的最小内容：健康检查、静态首页、SSE 流式验证。
对局引擎（12 轮状态机 / 结构化判分 / 输出安全层）在后续迭代中接入。
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import APP_ID, APP_VERSION
from .config import BASE_DIR, settings
from .llm import llm_client

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(APP_ID)

STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="AI 反诈劝阻",
    version=APP_VERSION,
    # 生产环境关闭 /docs 与 /openapi.json：对外暴露接口文档会被安全扫描告警
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
)


@app.on_event("startup")
async def _on_startup() -> None:
    logger.info("%s v%s 启动完成，监听端口 %s", APP_ID, APP_VERSION, settings.port)
    logger.info("大模型 provider=%s configured=%s", settings.llm.provider, settings.llm.configured)


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


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
