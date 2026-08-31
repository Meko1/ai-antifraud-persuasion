"""测试夹具。

签名密钥必须在导入 app.config 之前进入环境——它在模块加载时就被读走了，
这也正是"缺失则拒绝启动"那条约束的副作用。
"""

import os

os.environ.setdefault("STATE_SIGNING_SECRET", "test-secret-not-a-real-key")

# **测试不许连真实 Redis。** `app/config.py` 用 `load_dotenv()` 自动读项目根
# 目录那份 `.env`——而 2026-08-31 之后它带着大赛共享实例的真实凭证（打包
# 也要用它，见 package.sh 的 PACKAGE_INCLUDE_SECRETS）。`load_dotenv()` 默认
# 不覆盖已经存在的环境变量，所以在这里先占住 REDIS_URL，`app.stats.stats` /
# `app.guard.guard` 两个模块级单例造出来时看到的就是空值，跟本机 `.env`
# 填了什么无关。
#
# **不占住会怎样**：`Stats.enabled` 从 False 变 True，`/api/stats` 开始真的
# 尝试连 10.126.192.12（这台沙箱到不了那个内网地址），0.5 秒超时、
# fire-and-forget 的写入任务在某个用例的事件循环还没收尾就被回收——
# 实测两个用例单独跑、跟同类一起跑都是绿的，只有跑全量套件才红，
# 因为它们撞上的是**上一个用例遗留的、还没超时完的 Redis 连接尝试**，
# 跟这两个用例本身要测的东西毫无关系。
os.environ.setdefault("REDIS_URL", "")

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
