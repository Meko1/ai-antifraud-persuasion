"""测试夹具。

签名密钥必须在导入 app.config 之前进入环境——它在模块加载时就被读走了，
这也正是"缺失则拒绝启动"那条约束的副作用。
"""

import os

os.environ.setdefault("STATE_SIGNING_SECRET", "test-secret-not-a-real-key")

import pytest


@pytest.fixture(autouse=True)
def _isolate_guard():
    """每个用例开始前清空限流与一次性消费状态（app/guard.py）。

    **不清会出一种最难查的红。** `guard` 是模块级单例，限流按分钟固定窗口
    计数，默认 20 次开局/分钟；整个测试套件里 `POST /api/game/start` 远不止
    20 次，于是第 21 次开始返回 429——而失败的是那个**碰巧排在第 21 位**的
    用例，跟它测的东西毫无关系，只跟执行顺序有关。

    重放键同理：不同用例用同一个 gid 造令牌时会互相判成"已经打过了"。

    熔断器一并复位：一个用例把它打开之后，后面所有开局都会拿到 503。
    """
    from app.guard import breaker, guard

    guard.reset()
    breaker.reset()
    yield
    guard.reset()
    breaker.reset()
