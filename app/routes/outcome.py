"""结局回传面：宿主 App / 交易系统报回一条异动 24 小时后的结果。

判断在 `app/outcome.py`，这里只做鉴权、限流、把结果落进去。
"""

from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import APP_ID
from ..config import settings
from ..guard import guard
from ..http import client_key, too_many
from ..outcome import OutcomeError, RecordResult, outcome_store, parse_report

logger = logging.getLogger(APP_ID)

router = APIRouter()


# ── 结局回传 ──────────────────────────────────────────────────────────────
#
# `app/trigger.py` 一路带着 anomaly_id/subject_ref/arm 走完开局，却没有接口
# 告诉宿主 App 该往哪儿把"24 小时后这笔到底完成没有"传回来——核心指标
# （24h 内转出放弃率）因此即使真接了异动流水也算不出来。这个端点补的是
# 这段缺口：接收判断结果，不产生判断，也不需要新的服务端状态
# （`arm` 从 `subject_ref` 现算，见 app/outcome.py）。
#
# **鉴权**：调用方是宿主 App 的后台系统，不是玩家浏览器，没有状态令牌可用，
# 换成一个共享密钥。密钥未配置时直接 503——"没配"和"配错"要能分清楚，
# 静默放行任何请求等于任何人都能往这份统计里注水。

class OutcomeReportBody(BaseModel):
    anomaly_id: str = Field(max_length=128)
    subject_ref: str = Field(max_length=128)
    status: str = Field(max_length=32)
    observed_at: Optional[int] = None
    window_hours: Optional[int] = None
    trigger_type: str = Field(default="", max_length=64)


@router.post("/api/outcome/report")
async def outcome_report(request: Request, body: OutcomeReportBody) -> JSONResponse:
    """宿主 App / 交易系统回传一条异动的 24 小时后结果。见 app/outcome.py。"""
    secret = settings.outcome_report_secret
    if not secret:
        return JSONResponse(
            {"code": "not_configured", "message": "未配置 OUTCOME_REPORT_SECRET"},
            status_code=503,
        )
    # 常数时间比较：这是一个共享密钥，用 `==` 比较会把耗时差异泄露给
    # 一个逐字节猜密钥的攻击者
    provided = request.headers.get("x-outcome-secret", "")
    if not hmac.compare_digest(provided, secret):
        return JSONResponse({"code": "unauthorized"}, status_code=401)

    if not await guard.allow(
        f"outcome:{client_key(request)}", limit=settings.rate_limit_turn
    ):
        return too_many("outcome")

    try:
        report = parse_report(body.model_dump())
    except OutcomeError as exc:
        logger.info("拒绝非法回传: %s", exc)
        return JSONResponse(
            {"code": "invalid_report", "message": str(exc)}, status_code=400
        )

    result = await outcome_store.record(report)
    if result is RecordResult.UNAVAILABLE:
        # 这份数据没有第二个来源：Redis 不可用时要让宿主 App 知道重试，
        # 而不是回一个 200 假装记下来了（那样这条回传就永久丢了）
        return JSONResponse(
            {
                "code": "storage_unavailable",
                "message": "统计存储不可用，这份回传没有第二个来源，请重试",
            },
            status_code=503,
        )
    if result is RecordResult.CONFLICT:
        # 同一条异动已经记过一个不同的结果——静默覆盖比丢一笔更糟：
        # 会把两个互相矛盾的状态里的一个悄悄抹掉，而调用方毫无察觉
        return JSONResponse(
            {
                "code": "outcome_conflict",
                "message": "这条异动已记过一个不同的结果，本次回传被拒绝、不会覆盖",
            },
            status_code=409,
        )
    # DUPLICATE 与 STORED 对宿主来说都是"这份回传已经落地"——
    # 重试不该收到跟第一次不一样的响应，否则宿主没法把重试逻辑写简单。
    # `duplicate` 只是告诉它这次没有产生新计数，供排查用，不影响 `ok`。
    return JSONResponse({
        "ok": True,
        "arm": report.arm.value,
        "duplicate": result is RecordResult.DUPLICATE,
    })
