"""结局回传契约（app/outcome.py）。

这份测试钉两样：
1. 契约本身（缺字段、非法值一律在入口拒掉，不猜一个最接近的）——
   与 tests/test_trigger.py 对 `build_context` 的态度完全一致。
2. `arm` 现算而不是另存一份映射（ADR-0003 同一条原则）：同一个
   `subject_ref` 报多少次结果，都必须落进同一个 arm。
"""

import asyncio
import dataclasses
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.config import settings as real_settings
from app.main import app
from app.outcome import (
    OutcomeError,
    OutcomeReport,
    OutcomeStatus,
    OutcomeStore,
    RecordResult,
    parse_report,
)
from app.trigger import Arm, assign_arm


class Test契约:
    def _payload(self, **over):
        base = dict(
            anomaly_id="AN-2026-0823-771",
            subject_ref="u_8f3a91",
            status="abandoned",
        )
        base.update(over)
        return base

    def test_完整回传能解析(self) -> None:
        report = parse_report(self._payload())
        assert report.anomaly_id == "AN-2026-0823-771"
        assert report.status is OutcomeStatus.ABANDONED
        assert report.window_hours == 24, "不传就是默认的 24 小时观测窗"

    def test_缺anomaly_id直接拒(self) -> None:
        with pytest.raises(OutcomeError, match="anomaly_id"):
            parse_report(self._payload(anomaly_id=""))

    def test_缺subject_ref直接拒(self) -> None:
        """没有它就算不出 arm，这份回传就没法归类——理由与 trigger.py 同源。"""
        with pytest.raises(OutcomeError, match="subject_ref"):
            parse_report(self._payload(subject_ref=""))

    def test_未知状态直接拒而不是落到某个默认档(self) -> None:
        with pytest.raises(OutcomeError, match="status"):
            parse_report(self._payload(status="cancelled"))

    def test_可以带上可选的异动类型(self) -> None:
        report = parse_report(self._payload(trigger_type="fund_redemption"))
        assert report.trigger_type == "fund_redemption"

    def test_不给异动类型也行(self) -> None:
        """可选字段，宿主不愿意给也不该被拒。"""
        assert parse_report(self._payload()).trigger_type == ""

    def test_拼错的异动类型直接拒而不是悄悄开一个新键(self) -> None:
        """**不校验的话，一个拼错的类型会在 Redis 里开一个新键**，
        按类型拆分的统计就此永久碎成两份，没有任何一处报过错——
        与 `trigger.build_context` 对 `trigger_type` 的态度必须一致。
        """
        with pytest.raises(OutcomeError, match="trigger_type"):
            parse_report(self._payload(trigger_type="清仓"))

    def test_自定义观测窗口照收(self) -> None:
        """撤单率的观测窗口与放弃率不同，不该被硬编码的 24 卡死。"""
        report = parse_report(self._payload(window_hours=48))
        assert report.window_hours == 48


class TestArm现算:
    def test_arm与trigger里算出来的一致(self) -> None:
        """**不需要新的服务端状态**——这是这个模块存在的全部理由。

        开局时 `trigger.assign_arm(subject_ref)` 算过一次，24 小时后回传
        再算一次，两次必须是同一个值，否则这条数据就没法按组归类。
        """
        report = parse_report(dict(
            anomaly_id="A", subject_ref="u_8f3a91", status="completed",
        ))
        assert report.arm is assign_arm("u_8f3a91")

    def test_同一个subject多次回传落在同一组(self) -> None:
        first = parse_report(dict(
            anomaly_id="A1", subject_ref="u_1", status="abandoned",
        )).arm
        again = parse_report(dict(
            anomaly_id="A2", subject_ref="u_1", status="completed",
        )).arm
        assert first is again


class TestRedis不可用时安全:
    """与 `stats.Stats` 同一条原则：Redis 没配、连不上都不该让调用方炸掉。

    与 `stats.Stats` 不同的一点单独测：这里的失败要变成一个调用方能看到的
    `False`，而不是像统计那样悄悄吞掉——见 `OutcomeStore.record` 的文档字符串。
    """

    async def test_未配置时record返回不可用(self) -> None:
        store = OutcomeStore("")
        report = OutcomeReport(
            anomaly_id="A", subject_ref="u_1",
            status=OutcomeStatus.ABANDONED, observed_at=0,
        )
        assert await store.record(report) is RecordResult.UNAVAILABLE

    async def test_未配置时snapshot返回不可用(self) -> None:
        store = OutcomeStore("")
        assert await store.snapshot() == {"available": False}

    def test_未配置时enabled为False(self) -> None:
        assert OutcomeStore("").enabled is False


def _with_secret(secret: str):
    """`Settings` 是 frozen dataclass，整份换掉再指过去——
    与 tests/test_api_contract.py 的 `_settings()` 同一个做法。
    """
    return dataclasses.replace(real_settings, outcome_report_secret=secret)


class _FakeStore:
    """端点鉴权/校验测试不需要真的碰 Redis，只需要一个能控制结果的替身。"""

    def __init__(self, *, result: RecordResult) -> None:
        self._result = result
        self.recorded: list = []

    async def record(self, report: OutcomeReport) -> RecordResult:
        self.recorded.append(report)
        return self._result


class Test端点鉴权与校验:
    """调用方是宿主 App 的后台系统，不是玩家浏览器——没有状态令牌可用，
    鉴权换成共享密钥。"""

    def test_未配置密钥直接503(self, monkeypatch) -> None:
        monkeypatch.setattr("app.main.settings", _with_secret(""))
        client = TestClient(app)
        resp = client.post(
            "/api/outcome/report",
            json={"anomaly_id": "A", "subject_ref": "u_1", "status": "abandoned"},
        )
        assert resp.status_code == 503
        assert resp.json()["code"] == "not_configured"

    def test_密钥不对返回401(self, monkeypatch) -> None:
        monkeypatch.setattr("app.main.settings", _with_secret("right-secret"))
        client = TestClient(app)
        resp = client.post(
            "/api/outcome/report",
            json={"anomaly_id": "A", "subject_ref": "u_1", "status": "abandoned"},
            headers={"X-Outcome-Secret": "wrong"},
        )
        assert resp.status_code == 401

    def test_密钥对但字段非法返回400(self, monkeypatch) -> None:
        monkeypatch.setattr("app.main.settings", _with_secret("right-secret"))
        client = TestClient(app)
        resp = client.post(
            "/api/outcome/report",
            json={
                "anomaly_id": "A", "subject_ref": "u_1", "status": "cancelled",
            },
            headers={"X-Outcome-Secret": "right-secret"},
        )
        assert resp.status_code == 400

    def test_合法回传返回arm(self, monkeypatch) -> None:
        monkeypatch.setattr("app.main.settings", _with_secret("right-secret"))
        fake = _FakeStore(result=RecordResult.STORED)
        monkeypatch.setattr("app.main.outcome_store", fake)
        client = TestClient(app)
        resp = client.post(
            "/api/outcome/report",
            json={
                "anomaly_id": "A", "subject_ref": "u_8f3a91", "status": "abandoned",
            },
            headers={"X-Outcome-Secret": "right-secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["arm"] == assign_arm("u_8f3a91").value
        assert body["duplicate"] is False
        assert len(fake.recorded) == 1

    def test_重复回传返回200且标记duplicate(self, monkeypatch) -> None:
        """P0-1：宿主重试同一条结果，不该收到跟第一次不一样的响应——
        `ok` 照样是 True，只是 `duplicate` 告诉它这次没有产生新计数。
        """
        monkeypatch.setattr("app.main.settings", _with_secret("right-secret"))
        monkeypatch.setattr(
            "app.main.outcome_store", _FakeStore(result=RecordResult.DUPLICATE)
        )
        client = TestClient(app)
        resp = client.post(
            "/api/outcome/report",
            json={"anomaly_id": "A", "subject_ref": "u_1", "status": "abandoned"},
            headers={"X-Outcome-Secret": "right-secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["duplicate"] is True

    def test_冲突回传返回409且不假装成功(self, monkeypatch) -> None:
        """同一条异动此前已经记过一个不同的状态——拒绝，不静默覆盖。"""
        monkeypatch.setattr("app.main.settings", _with_secret("right-secret"))
        monkeypatch.setattr(
            "app.main.outcome_store", _FakeStore(result=RecordResult.CONFLICT)
        )
        client = TestClient(app)
        resp = client.post(
            "/api/outcome/report",
            json={"anomaly_id": "A", "subject_ref": "u_1", "status": "completed"},
            headers={"X-Outcome-Secret": "right-secret"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "outcome_conflict"

    def test_存储不可用时返回503且不假装成功(self, monkeypatch) -> None:
        """这份数据没有第二个来源，不能悄悄吞掉失败——与 stats 的旁路哲学不同,
        见 `app.outcome.OutcomeStore.record` 的文档字符串。
        """
        monkeypatch.setattr("app.main.settings", _with_secret("right-secret"))
        monkeypatch.setattr(
            "app.main.outcome_store", _FakeStore(result=RecordResult.UNAVAILABLE)
        )
        client = TestClient(app)
        resp = client.post(
            "/api/outcome/report",
            json={"anomaly_id": "A", "subject_ref": "u_1", "status": "abandoned"},
            headers={"X-Outcome-Secret": "right-secret"},
        )
        assert resp.status_code == 503
        assert resp.json()["code"] == "storage_unavailable"


class FakePipeline:
    """够用就好：只实现 outcome.py 真正用到的两个命令。与
    tests/test_stats.py 的同名类同一个套路，两边不共用是因为
    存的语义不同（这里的 store 还混着 dedup 键的裸字符串）。
    """

    def __init__(self, store: Dict[str, Any]) -> None:
        self._store = store
        self._ops: List[Any] = []

    def hincrby(self, key: str, field: str, amount: int) -> "FakePipeline":
        def run() -> None:
            bucket = self._store.setdefault(key, {})
            bucket[field] = bucket.get(field, 0) + amount

        self._ops.append(run)
        return self

    def hgetall(self, key: str) -> "FakePipeline":
        self._ops.append(lambda: dict(self._store.get(key, {})))
        return self

    async def execute(self) -> List[Any]:
        return [op() for op in self._ops]


class FakeRedis:
    """`decode_responses=True` 的真实客户端返回 str；这个替身也一律存/取
    str，好让 `record()` 里 `previous == report.status.value` 的比较成立。
    """

    def __init__(self) -> None:
        self.store: Dict[str, Any] = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self.store)

    async def set(self, key: str, value: str, nx: bool = False, ex: int = 0) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key: str) -> Any:
        return self.store.get(key)


def _wired(client: object) -> OutcomeStore:
    s = OutcomeStore("redis://fake/0")
    s._client = client
    return s


class TestRecord幂等与分层:
    """P0-1：同一条结果重复上报只计一次，冲突不覆盖，
    `by_trigger` 能按 `trigger_type × arm` 读出来。
    """

    def _report(self, **over: Any) -> OutcomeReport:
        base: Dict[str, Any] = dict(
            anomaly_id="AN-1", subject_ref="u_8f3a91",
            status=OutcomeStatus.ABANDONED, observed_at=0,
            trigger_type="fund_redemption",
        )
        base.update(over)
        return OutcomeReport(**base)

    def test_首次上报计入对应arm与trigger(self) -> None:
        async def scenario() -> tuple:
            store = _wired(FakeRedis())
            result = await store.record(self._report())
            return result, await store.snapshot()

        result, snap = asyncio.run(scenario())
        assert result is RecordResult.STORED
        arm = assign_arm("u_8f3a91").value
        assert snap[arm] == {"abandoned": 1}
        assert snap["by_trigger"]["fund_redemption"][arm] == {"abandoned": 1}

    def test_相同状态重复上报不重复计数(self) -> None:
        """验收标准：同一回传重复 N 次只计一条。"""

        async def scenario() -> tuple:
            store = _wired(FakeRedis())
            results = [await store.record(self._report()) for _ in range(10)]
            return results, await store.snapshot()

        results, snap = asyncio.run(scenario())
        assert results[0] is RecordResult.STORED
        assert all(r is RecordResult.DUPLICATE for r in results[1:])
        arm = assign_arm("u_8f3a91").value
        assert snap[arm] == {"abandoned": 1}

    def test_同一条异动报出两个不同状态判冲突不覆盖(self) -> None:
        async def scenario() -> tuple:
            store = _wired(FakeRedis())
            first = await store.record(self._report(status=OutcomeStatus.ABANDONED))
            second = await store.record(self._report(status=OutcomeStatus.COMPLETED))
            return first, second, await store.snapshot()

        first, second, snap = asyncio.run(scenario())
        assert first is RecordResult.STORED
        assert second is RecordResult.CONFLICT
        arm = assign_arm("u_8f3a91").value
        assert snap[arm] == {"abandoned": 1}, "冲突的那次不落地，原状态不能被悄悄覆盖"

    def test_不同观测窗口各自独立计数(self) -> None:
        """24 小时放弃率和 48 小时撤单率是两件事，不该共用一个去重键。"""

        async def scenario() -> tuple:
            store = _wired(FakeRedis())
            first = await store.record(self._report(window_hours=24))
            second = await store.record(self._report(window_hours=48))
            return first, second

        first, second = asyncio.run(scenario())
        assert first is RecordResult.STORED
        assert second is RecordResult.STORED

    def test_snapshot按trigger乘arm分层且跳过空组合(self) -> None:
        async def scenario() -> Dict[str, Any]:
            store = _wired(FakeRedis())
            await store.record(self._report(
                anomaly_id="AN-1", trigger_type="fund_redemption",
                status=OutcomeStatus.ABANDONED,
            ))
            await store.record(self._report(
                anomaly_id="AN-2", trigger_type="large_transfer_out",
                status=OutcomeStatus.COMPLETED,
            ))
            return await store.snapshot()

        snap = asyncio.run(scenario())
        assert set(snap["by_trigger"]) == {"fund_redemption", "large_transfer_out"}
        arm = assign_arm("u_8f3a91").value
        assert snap["by_trigger"]["fund_redemption"] == {arm: {"abandoned": 1}}
        assert snap["by_trigger"]["large_transfer_out"] == {arm: {"completed": 1}}

    def test_没有trigger_type时不写by_trigger(self) -> None:
        async def scenario() -> Dict[str, Any]:
            store = _wired(FakeRedis())
            await store.record(self._report(anomaly_id="AN-3", trigger_type=""))
            return await store.snapshot()

        snap = asyncio.run(scenario())
        assert snap["by_trigger"] == {}
