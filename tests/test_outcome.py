"""结局回传契约（app/outcome.py）。

这份测试钉两样：
1. 契约本身（缺字段、非法值一律在入口拒掉，不猜一个最接近的）——
   与 tests/test_trigger.py 对 `build_context` 的态度完全一致。
2. `arm` 现算而不是另存一份映射（ADR-0003 同一条原则）：同一个
   `subject_ref` 报多少次结果，都必须落进同一个 arm。
"""

import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.config import settings as real_settings
from app.main import app
from app.outcome import OutcomeError, OutcomeReport, OutcomeStatus, OutcomeStore, parse_report
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

    async def test_未配置时record返回False(self) -> None:
        store = OutcomeStore("")
        report = OutcomeReport(
            anomaly_id="A", subject_ref="u_1",
            status=OutcomeStatus.ABANDONED, observed_at=0,
        )
        assert await store.record(report) is False

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
    """端点鉴权/校验测试不需要真的碰 Redis，只需要一个能控制成败的替身。"""

    def __init__(self, *, ok: bool) -> None:
        self._ok = ok
        self.recorded: list = []

    async def record(self, report: OutcomeReport) -> bool:
        self.recorded.append(report)
        return self._ok


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
        fake = _FakeStore(ok=True)
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
        assert len(fake.recorded) == 1

    def test_存储不可用时返回503且不假装成功(self, monkeypatch) -> None:
        """这份数据没有第二个来源，不能悄悄吞掉失败——与 stats 的旁路哲学不同,
        见 `app.outcome.OutcomeStore.record` 的文档字符串。
        """
        monkeypatch.setattr("app.main.settings", _with_secret("right-secret"))
        monkeypatch.setattr("app.main.outcome_store", _FakeStore(ok=False))
        client = TestClient(app)
        resp = client.post(
            "/api/outcome/report",
            json={"anomaly_id": "A", "subject_ref": "u_1", "status": "abandoned"},
            headers={"X-Outcome-Secret": "right-secret"},
        )
        assert resp.status_code == 503
        assert resp.json()["code"] == "storage_unavailable"
