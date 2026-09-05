"""重放、幂等、限流（app/guard.py）。

三件事共用一个原语，但**语义不同，测试也要分开钉**：

| 键                   | 谁调             | 要求                               |
|----------------------|------------------|------------------------------------|
| `replay:<签名>`      | 进门只读、出门标记 | 失败重试必须还能打                 |
| `turn:<gid>:<round>` | `score` 那一刻   | 断流重试不许写第二遍               |
| `rate:<来源>`        | 每个请求         | 到量拒绝，跨窗口自动放行           |

最要紧的是**第一条那个"只读"**：原实现是"进门就标记"的话，网关抖一下
导致这一轮失败时，玩家刚说的那句话就永远发不出去了。
"""

import time

import pytest

from app.guard import Guard, _LocalClaims, _LocalCounters, replay_key, turn_key


@pytest.fixture()
def guard() -> Guard:
    """没配 Redis 的 Guard —— 走单进程兜底那条路。

    共享存储那条路要真的连 Redis 才测得动，而这个仓库的测试不依赖外部服务
    （与 stats / transcripts 同一条纪律）。**兜底这条路本身也必须是对的**：
    README 写着 Redis 是选填，那种部署下跑的就是它。
    """
    return Guard("")


class Test一次性消费:
    async def test_第一次拿到第二次拿不到(self, guard: Guard) -> None:
        assert await guard.claim("k", ttl=60) is True
        assert await guard.claim("k", ttl=60) is False

    async def test_seen是只读的(self, guard: Guard) -> None:
        """**这一条是重放防护正确性的全部。**

        `seen` 要是顺手把键标上了，一轮失败之后玩家就再也发不出那句话——
        而那一轮压根没打成。
        """
        assert await guard.seen("k") is False
        assert await guard.seen("k") is False, "问两遍不该把它问成'见过'"
        assert await guard.claim("k", ttl=60) is True, "问过之后仍然认领得到"

    async def test_不同的键互不影响(self, guard: Guard) -> None:
        assert await guard.claim("a", ttl=60) is True
        assert await guard.claim("b", ttl=60) is True

    def test_过期之后可以再认领(self) -> None:
        local = _LocalClaims()
        now = time.time()
        assert local.claim("k", 10, now) is True
        assert local.claim("k", 10, now + 5) is False
        assert local.claim("k", 10, now + 11) is True, "过了 TTL 就该放行"

    def test_到容量丢最旧的(self) -> None:
        local = _LocalClaims(limit=3)
        now = time.time()
        for i in range(5):
            local.claim(f"k{i}", 60, now)
        assert local.seen("k0", now) is False, "最旧的该被挤掉"
        assert local.seen("k4", now) is True


class Test限流:
    async def test_到量就拒(self, guard: Guard) -> None:
        for _ in range(3):
            assert await guard.allow("ip", limit=3) is True
        assert await guard.allow("ip", limit=3) is False

    async def test_零表示不限(self, guard: Guard) -> None:
        """本机调试用。**默认值不是 0**——默认要是不限，
        限流就成了一个"需要记得打开"的功能，而那种功能上线当天都是关着的。"""
        for _ in range(50):
            assert await guard.allow("ip", limit=0) is True

    async def test_不同来源各算各的(self, guard: Guard) -> None:
        assert await guard.allow("a", limit=1) is True
        assert await guard.allow("b", limit=1) is True, "别人打满了不该影响这一个"

    def test_跨窗口自动放行(self) -> None:
        counters = _LocalCounters()
        now = time.time()
        window = 60
        # 同一个窗口里加到 3
        for _ in range(3):
            counters.bump("k", window, now)
        assert counters.bump("k", window, now) == 4
        # 下一个窗口从头开始
        assert counters.bump("k", window, now + window) == 1


class Test键名:
    def test_重放键只取签名部分(self) -> None:
        """令牌正文可能有几 KB（含整局对话），不必也不该存进来。"""
        assert replay_key("payload.SIGNATURE") == "replay:SIGNATURE"

    def test_业务幂等键按局与轮(self) -> None:
        """**统计与语料认同一个键**，两边才不会各写各的。"""
        assert turn_key("abc", 3) == "turn:abc:3"
        assert turn_key("abc", 3) != turn_key("abc", 4)


class Test降级:
    async def test_没配redis时不是共享的且如实上报(self, guard: Guard) -> None:
        """`/healthz` 要把这一位报出来：多 worker 部署时它决定了
        重放防护是真的在生效，还是只在各自的进程里生效。"""
        assert guard.shared is False

    async def test_redis连不上时退回单进程而不是拦住服务(self) -> None:
        """**fail-open，不是 fail-closed。**

        投票日 Redis 抖一下，代价是那段时间重放防护退化回单进程；
        而 fail-closed 的代价是所有人都打不了。前者是有日志的降级，后者是事故。
        """
        broken = Guard("redis://127.0.0.1:1/0")  # 这个端口上不会有 Redis
        assert broken.shared is True, "配了就认为是共享的，真假要连了才知道"
        assert await broken.claim("k", ttl=60) is True, "连不上也要放行"
        assert await broken.claim("k", ttl=60) is False, "而且退回去的那一份仍然有效"
        assert await broken.allow("ip", limit=1) is True
        assert await broken.allow("ip", limit=1) is False


class Test连续故障熔断:
    """P1-13 的另一半。

    引擎对网关故障的处理是优雅降级（L1 兜底台词、L2 中性判分），
    那在**单局**尺度上是对的。但网关整个挂掉时，它意味着每个新点进来的用户
    都会拿到一局全程降级的对话，然后在复盘里读到一个由此推算出来的"结局"——
    技术故障被包装成了用户表现，而他不知道。
    """

    def test_偶发失败不熔断(self) -> None:
        """**判据是"连续"不是"累计"。** 偶发失败本来就该被降级吸收。"""
        from app.guard import Breaker

        b = Breaker(threshold=3)
        for _ in range(20):
            b.record(ok=False)
            b.record(ok=False)
            b.record(ok=True)
            assert b.open is False

    def test_连续失败到阈值就熔断(self) -> None:
        from app.guard import Breaker

        b = Breaker(threshold=3)
        b.record(ok=False)
        b.record(ok=False)
        assert b.open is False
        b.record(ok=False)
        assert b.open is True

    def test_一次成功就恢复(self) -> None:
        from app.guard import Breaker

        b = Breaker(threshold=2)
        b.record(ok=False)
        b.record(ok=False)
        assert b.open is True
        b.record(ok=True)
        assert b.open is False, "网关回来了就该立刻恢复发放"
