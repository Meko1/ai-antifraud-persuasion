"""服务入口。

平台硬约束：对外 Web 服务固定监听 21818，`http://ip:21818/` 必须能直接打开。

**这个文件只做装配。** 它知道这个服务由哪几块拼起来、按什么顺序挂上去，
不知道任何一条业务规则：

    app/http.py           安全响应头 · 限流分桶 · SSE 编码 · 请求上限
    app/routes/game.py    开局 / 一轮（SSE）/ 退出
    app/routes/ops.py     存活 / 就绪 / 全局统计 / 流式自检
    app/routes/outcome.py 24 小时后结果回传
    app/engine.py         对局编排        app/scoring.py  判分

2026-09-05 拆的。拆之前这里 869 行，上面五件事全在一处——**唯一能确定
一次改动影响到哪几条接口的办法是通读全文件**。路由路径、响应体、
中间件顺序一个字节没变，只是不再住在同一个文件里。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import APP_ID, APP_VERSION
from .config import BASE_DIR, settings
from .guard import guard
from .http import SECURITY_HEADERS
from .provenance import versions
from .routes import game, ops, outcome
from .routes.game import get_gateway  # noqa: F401  —— 测试按这个名字换网关替身

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(APP_ID)

STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """启动横幅。

    2026-09-05 从 `@app.on_event("startup")` 换成 lifespan：那个装饰器在
    当前 FastAPI 上已经弃用，每次跑测试都会打一条 DeprecationWarning——
    **一条永远存在的告警等于没有告警**，真出现新的弃用项没人会看见。
    行为一字不差：这段仍然只在启动时跑一次，收尾侧什么都不做。
    """
    # 离线演示模式的横幅。**必须在启动日志里**，不能只在模块导入时打——
    # 一个看不出来是演示的演示是骗局，而启动日志是运维唯一会看的那一处。
    if settings.offline_demo:
        logger.warning(
            "离线演示模式已开启：台词为预置内容，不调用大模型。判分照常（ADR-0001）。"
        )
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
    yield


app = FastAPI(
    title="AI 反诈劝阻",
    version=APP_VERSION,
    # 生产环境关闭 /docs 与 /openapi.json：对外暴露接口文档会被安全扫描告警
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
    lifespan=lifespan,
)


@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    # setdefault 语义：不覆盖某个响应自己已经设好的头
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


# 挂载顺序不影响路由匹配（三组路径互不相交），按"用户会碰到的先"排。
app.include_router(game.router)
app.include_router(ops.router)
app.include_router(outcome.router)


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
