"""安全响应头的两条分支。

## 这份文件为什么存在

`build_security_headers()` 的 docstring 写着「做成纯函数是为了让两条分支都能
被测到」——**然后一条都没测**（2026-09-07 补）。这一层此前零覆盖。

代价在 2026-09-07 兑现了：平台的人反馈「作品展示」那一栏嵌不进来。他截图
指向 `X-Content-Type-Options: nosniff`，那条与 iframe 无关；真正拦下来的是
白名单只写了 `https://ai-creator.eastmoney.com`，而那个门户 http 与 https
两个入口都活着，CSP 的 origin 匹配又带协议——于是停在 https 的人看得见，
停在 http 的人看到一块空白，**而作者手里那个链接是 https 的，自己撞不到**。

下面钉三件事：两条分支各自的形状，以及"配了白名单就必须一并放宽 CORP"
那个半修的坑（`app/http.py` 里 `else` 分支上面那段注释说的正是它）。
"""

from __future__ import annotations

import pathlib

from app.http import build_security_headers


def _directive(csp: str, name: str) -> str:
    """从 CSP 串里取出某一条指令的值。"""
    for part in csp.split("; "):
        if part.split(" ")[0] == name:
            return part[len(name) + 1 :]
    raise AssertionError(f"CSP 里没有 {name}：{csp}")


def test_不配白名单时谁都不许嵌() -> None:
    h = build_security_headers("")
    assert _directive(h["Content-Security-Policy"], "frame-ancestors") == "'none'"
    # 老浏览器那一半。CSP 没生效时它是唯一的防线
    assert h["X-Frame-Options"] == "DENY"
    assert h["Cross-Origin-Resource-Policy"] == "same-origin"


def test_配了白名单就整条撤掉_X_Frame_Options() -> None:
    # X-Frame-Options 只认单一 origin，留着会和 CSP 打架，
    # 而 CSP 的 frame-ancestors 在现代浏览器里优先级更高
    h = build_security_headers("'self' https://a.example http://a.example")
    assert "X-Frame-Options" not in h
    assert (
        _directive(h["Content-Security-Policy"], "frame-ancestors")
        == "'self' https://a.example http://a.example"
    )


def test_放开嵌入就得一并放宽_CORP_否则只修了一半() -> None:
    # `same-origin` 的 CORP 也拦 iframe 导航。放开 frame-ancestors 却留着它，
    # 那一栏照样是空白，而排查成本比一开始就没改还高
    h = build_security_headers("https://a.example")
    assert h["Cross-Origin-Resource-Policy"] == "cross-origin"


def test_nosniff_两条分支都在_它与_iframe_无关() -> None:
    # 平台反馈时指的就是这条。它只管 MIME 嗅探，删了是白送一个真漏洞，
    # 而对嵌入毫无帮助——两条分支都必须带着它
    for value in ("", "https://a.example"):
        assert build_security_headers(value)["X-Content-Type-Options"] == "nosniff"


def test_env_example_的示例要带引号且两种协议都列() -> None:
    """`.env.example` 那段填法说明本身就是这次事故的补丁，别被人改回去。

    两条都踩过：
      · 不加外层双引号 → start.sh 那句 `. "${ENV_FILE}"` 会把域名当命令执行，
        变量只拿到 `self`，CSP 悄悄退回 `'none'`（2026-08-31）
      · 只写 https → 停在 http 入口的人看到一块空白（2026-09-07）
    """
    text = pathlib.Path(__file__).resolve().parents[1].joinpath(".env.example").read_text(
        encoding="utf-8"
    )
    示例 = [
        line.strip().lstrip("#").strip()
        for line in text.splitlines()
        if "FRAME_ANCESTORS=" in line and line.strip().startswith("#")
    ]
    assert 示例, ".env.example 里那条填法示例不见了"
    for line in 示例:
        assert '="' in line and line.endswith('"'), f"示例没用双引号包住：{line}"
        assert "https://" in line and "http://" in line, f"示例只列了一种协议：{line}"
