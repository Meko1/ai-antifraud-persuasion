"""并发一高就「暂时无法接入客户」（2026-09-11）。

## 症状

展示当天并发一上来，随机几位用户在第一屏就被挡住：前端
（static/opening.js）打出「暂时无法接入客户。请检查网络或服务状态后重新连接，
本局尚未开始。」那一屏的触发条件只有一个——`POST /api/game/start` 返回非 200。

## 根因（三层，越往后越致命）

1. **排队没有预算。** `async with ACT_GATE` 是无限期等的，而调用方的
   6 秒 / 10 秒预算在排队之前就开始走表。并发一超过闸门额度，后到的请求
   在信号量上干等到预算耗尽，抛出一个 `asyncio.TimeoutError`——
   它在下游看起来和"网关真的挂了"一模一样。**人越多，这台服务越是在
   给自己伪造故障证据。**

2. **熔断器把并发数当成了时间。** 判据原是"连续 8 次失败"，而并发下
   同一瞬间的 8 个请求一起失败就凑满了——那只是 20% 的失败率。

3. **张开之后它自己出不来。** 合上的唯一途径是 `record(ok=True)`，
   而那一行只在一轮对局里被调到。张开 → 新对局一律 503 → 没有新对局
   → 没有人能给它一次成功 → **永远张着，直到重启**。
   这一层把一阵子的拥塞变成了永久的不可用。

端到端复现（120 人爬坡进场 + 高峰后 5 位迟到者）在 `dbg_concurrency.py`；
这个文件守的是三层里每一层各自的接缝，跑得快、不用起服务。
"""

from __future__ import annotations

import asyncio

import pytest

from app.gateway import (
    ACT_QUEUE_BUDGET,
    CLASSIFY_QUEUE_BUDGET,
    ENDING_QUEUE_BUDGET,
    GatewayBusy,
    _admit,
)
from app.guard import Breaker


class Test排队有预算:
    """第 1 层：排不上号要当场让路，不能把调用方的预算耗在队列里。"""

    @pytest.mark.asyncio
    async def test_排不上号抛的是拥塞不是超时(self) -> None:
        """**这是整条链的分水岭。** 抛 `asyncio.TimeoutError` 的话，
        下游没有任何办法把"人多"和"网关挂了"分开。
        """
        gate = asyncio.Semaphore(1)
        await gate.acquire()  # 名额被占着，谁也进不来

        with pytest.raises(GatewayBusy):
            async with _admit(gate, 0.05, "演绎"):
                pass  # pragma: no cover - 进不来

    @pytest.mark.asyncio
    async def test_让路发生在调用方预算耗尽之前(self) -> None:
        """排队预算必须**明显短于**调用方的预算，否则让路让了个寂寞：
        等它超时的时候，演绎那 6 秒也已经没了。
        """
        assert ACT_QUEUE_BUDGET < 6.0, "演绎预算见 engine.ACT_TIMEOUT_SECONDS"
        assert CLASSIFY_QUEUE_BUDGET < 10.0, "分类预算见 engine.CLASSIFY_TIMEOUT_SECONDS"

    def test_结局那一屏排队排得更久(self) -> None:
        """一局只调一次，却是唯一会被截图发出去的一屏。拿逐轮那 2 秒卡它，
        等于拿最贵的一屏去省最不该省的地方。"""
        assert ENDING_QUEUE_BUDGET > ACT_QUEUE_BUDGET

    @pytest.mark.asyncio
    async def test_让路之后名额还给下一个人(self) -> None:
        """`wait_for` 取消一个等在 `Semaphore.acquire()` 上的协程之后，
        信号量不能漏名额——漏一个，这台服务的并发额度就永久少一格。
        """
        gate = asyncio.Semaphore(1)
        async with _admit(gate, 0.05, "演绎"):
            with pytest.raises(GatewayBusy):
                async with _admit(gate, 0.05, "演绎"):
                    pass  # pragma: no cover
        # 退出上一个 with 之后名额该还回来了
        async with _admit(gate, 0.05, "演绎"):
            pass


class Test熔断器判的是网关挂没挂不是忙不忙:
    """第 2 层：并发下的失败**比例**才是证据，连续计数不是。"""

    def test_一阵并发失败扎堆落下来不算网关挂了(self) -> None:
        """**这是并发下失败真正的形状，也是这条修复的核心一格。**

        40 个在飞的请求，先后成功了 32 个；网关一慢，剩下 8 个在同一瞬间
        一起超时——事件循环里它们就是连续的 8 次失败，中间插不进任何成功
        （成功的那些早就结算完了）。原来的"连续 8 次"在这里当场张开，
        而这只是 20% 的失败率，网关好得很。

        **计数器把并发数当成了时间。** 现在判的是窗口内的比例。
        """
        breaker = Breaker(threshold=8)
        for _ in range(32):
            breaker.record(ok=True)
        for _ in range(8):
            breaker.record(ok=False)

        assert not breaker.open, "20% 的失败率被当成了网关整个挂掉"

    def test_一次成功不清空历史(self) -> None:
        """专治"把成功清零改回去"的冲动。

        清零的话，"窗口内失败数 ≥ 阈值"就又退化成了"连续 N 次"——
        同一个 bug 换个写法回来。成功该做的是把**比例**压下去，不是把
        证据擦掉。
        """
        breaker = Breaker(threshold=3)
        breaker.record(ok=False)
        breaker.record(ok=False)
        breaker.record(ok=True)   # 清零的话下面两次就凑不满 3
        breaker.record(ok=False)

        assert breaker.snapshot()["failures"] == 3
        # 3 次失败 / 4 次样本 = 75%，刚好到线：**该张开**
        assert breaker.open

    def test_网关真的挂了照样张开(self) -> None:
        """修完不能把 P1-13 修没了：网关整个挂掉时仍然要停止发放新干预，
        否则每个新进来的人拿到的是一局全程罐头台词、全程中性判分的对话，
        然后在复盘里读到一个由此推算出来的"结局"。"""
        breaker = Breaker(threshold=8)
        for _ in range(8):
            breaker.record(ok=False)
        assert breaker.open

    def test_拥塞一个字都不记(self) -> None:
        """`busy=True` 是"压根没打到网关"。拿它熔断就是"人一多就关门"。"""
        breaker = Breaker(threshold=8)
        for _ in range(50):
            breaker.record(ok=False, busy=True)
        assert not breaker.open
        assert breaker.snapshot()["samples"] == 0


class Test张开之后自己合得上:
    """第 3 层。**这一层才是"项目不可用"那句话的出处**：故障是一阵子的，
    锁死是永久的。"""

    def test_冷静期内仍然挡着(self) -> None:
        breaker = Breaker(threshold=1, cooldown=60.0)
        breaker.record(ok=False)
        assert breaker.open

    def test_冷静期满自己放人进去探路(self) -> None:
        """**没有任何一局在打**——这正是原先出不来的那个局面：
        合上的唯一途径在对局里，而新对局一律被自己挡着。"""
        breaker = Breaker(threshold=1, cooldown=0.05)
        breaker.record(ok=False)
        assert breaker.open

        import time as _time

        _time.sleep(0.06)
        assert not breaker.open, "上游早好了，人却还进不来——这台服务只能重启"

    def test_探砸了会重新张开(self) -> None:
        """半开不是"从此放行"。探路的那一局要是照样失败，门得再关上。"""
        breaker = Breaker(threshold=2, cooldown=0.05)
        breaker.record(ok=False)
        breaker.record(ok=False)
        assert breaker.open

        import time as _time

        _time.sleep(0.06)
        assert not breaker.open  # 半开，放行
        breaker.record(ok=False)
        breaker.record(ok=False)
        assert breaker.open

    def test_窗口外的旧失败不算数(self) -> None:
        """一小时前那阵故障不该让现在的人进不来。"""
        breaker = Breaker(threshold=2, window=0.05)
        breaker.record(ok=False)

        import time as _time

        _time.sleep(0.06)
        breaker.record(ok=False)
        assert not breaker.open, "窗口只剩 1 次失败，凑不满阈值"


class Test熔断状态要能被看见:
    def test_healthz_报得出熔断张没张(self) -> None:
        """**兜底台词是故意让人察觉不出来的**，熔断状态因此必须自己报出来。
        在此之前用户那边写着「暂时无法接入客户」，运维这边 /healthz 一路绿，
        只能从"没人进得来"倒推。
        """
        breaker = Breaker(threshold=1)
        breaker.record(ok=False)
        snap = breaker.snapshot()
        assert snap["open"] is True
        assert snap["failures"] == 1
        assert snap["samples"] == 1
