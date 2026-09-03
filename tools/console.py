"""跑批脚本的终端输出防线。

## 起因

`python -m tools.balance_sim` 在 Windows 默认的 GBK 控制台上，
**全部场景的门槛都计算通过之后**，在打印最后那个 `✅` 时抛
`UnicodeEncodeError`，进程退出码 1。

于是开发者看到的是"平衡门禁失败"，而实际上一个数都没错。项目主要开发环境
是 Windows，这个假红会一直消耗复核的注意力——**一个会说谎的验证工具
比没有验证工具更糟**。

## 两条一起做

1. `guard()` 把 stdout/stderr 的编码错误处理器换成 `replace`。
   **不改编码**——改成 UTF-8 只会让 GBK 终端上的中文变成乱码，
   那是把一个崩溃换成一屏看不懂的字。保留终端自己的编码、
   只让编不出来的那个字符变成 `?`，是唯一不伤及其余输出的做法。
2. 结论行改用 ASCII 记号（`OK` / `FAIL`）。这一行是 CI 日志和人眼
   唯一真正要读的东西，它不该依赖终端支持什么字符。
   表格里的框线字符留着——GBK 认识它们，而且它们只是装饰。
"""

from __future__ import annotations

import sys

# 结论记号。**ASCII，且成对定义在一处**——散在各个脚本里迟早会有一个
# 被改成表情符号，而那正是这个模块存在的原因。
OK = "OK"
FAIL = "FAIL"


def guard() -> None:
    """让编不出来的字符降级成 `?`，而不是让进程崩掉。

    在每个 CLI 的 `main()` 第一行调用。幂等，重复调用无害。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - 被重定向成非 TextIO 时
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 极少数被包装过的流
            pass
