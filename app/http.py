"""HTTP 层的公共件：安全响应头、限流分桶、SSE 编码、请求上限。

**这里只有"每条路由都要用、但不属于任何一条路由"的东西。** 业务在
`app/routes/` 下按面分文件，编排在 `app/engine.py`，判分在 `app/scoring.py`。

2026-09-05 从 `main.py` 里抽出来。抽之前 `main.py` 是 869 行，
安全头、限流、三个业务面、两个探针挤在一处——**唯一能确定改动影响范围的
办法是通读全文件**。抽的是文件，路由与响应一个字节没变。
"""

from __future__ import annotations

import json
import os
from typing import Dict

from fastapi import Request
from fastapi.responses import JSONResponse


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
#
# ── 2026-09-07：**协议是 origin 的一部分**，白名单要连协议一起列 ──────────
#
# 平台的人反馈「作品展示」那一栏嵌不进来，他截的图指向 `X-Content-Type-Options:
# nosniff`——那条与 iframe 无关（它只管 MIME 嗅探）。真正拦下来的是这里：
# 当时的白名单只写了 `https://ai-creator.eastmoney.com`，而那个门户
# **http 与 https 两个入口都活着**（实测 `https://ai-creator.eastmoney.com/`
# 会 302 到 `http://ai-creator.eastmoney.com/portal/`，那一跳再 302 回 https）。
# CSP 的 origin 匹配带协议，`https://x` 不匹配 `http://x`——于是同一个页面，
# 停在 https 的人看得见，停在 http 的人看到一块空白。
#
# **这类 bug 作者自己永远撞不到**：他手里那个链接是 https 的。
# 所以白名单一律把 http/https 两种都写上（`.env.example` 那段同批改了）。
#
# 顺带记一笔，这一条我们改不了、要平台自己修：他们的 Tengine 对不带尾斜杠的
# 地址回 `301 → http://…/`（没吃 X-Forwarded-Proto），https 的父页面跟着跳
# 到 http 子框架就是混合内容，浏览器直接拦。iframe 的 src 带上尾斜杠可以绕开。
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


# ── 限流 ──────────────────────────────────────────────────────────────────
#
# 接口全部无鉴权（这个作品当前没有宿主 App 的可信身份可用），所以限流是
# 唯一那道闸。按来源 IP 分桶——它挡不住一个铁了心的人，但挡得住
# "一条 curl 循环把共享网关额度吃光"，而后者才是投票日真实会发生的事。
#
# 真实接入之后这里要换成宿主 App 的用户与设备维度，IP 那一层保留做兜底。
#
# 单例定义在 app/guard.py（测试要复位它，见 tests/conftest.py）。


def client_key(request: Request) -> str:
    """限流的分桶键。

    优先取反代给的 `X-Forwarded-For` 第一跳——平台是 nginx 直连，
    不取的话所有人都会落进同一个桶，限流会变成"全站共用一个额度"。
    伪造它很容易，但伪造之后打散的是攻击者自己的桶，不影响正常用户。
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def too_many(scope: str) -> JSONResponse:
    return JSONResponse(
        {"code": "rate_limited", "message": "请求过于频繁，请稍后再试。"},
        status_code=429,
        headers={"Retry-After": "60"},
    )


# 玩家一句话的上限。前端输入框写了 maxlength="120"，但那只是前端——
# 一条 curl 就能把几十 KB 塞进提示词，直接吃掉共享的网关额度。
# 给到 200 是留出前端限制之外的余量，不是放宽玩法。
MAX_UTTERANCE_CHARS = 200

# 令牌上限。它随 history 增长（满局的发言与台词都在里面），12 轮口径实测满局约 7 KB，
# 收到 10 轮之后只会更小——这个上限留着不动，它是护栏不是预算。
MAX_TOKEN_CHARS = 32768


def sse(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
line_sources: Dict[str, int] = {
    "model": 0, "fallback": 0, "absorbed": 0, "safety_escalation": 0,
}
