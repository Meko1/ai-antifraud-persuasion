"""压测器自身的算术（§10.3）。

压测本体要连真实服务、真实花钱，不进 CI。但"怎么算 P95"和"什么算通过"
是纯函数，必须守住——门槛判错的压测器比没有压测器更坏：它会盖章放行。

规格见 docs/TECH-DESIGN.md §9.4、§10.3。
"""

from tools.loadtest import P95_CEILING_SECONDS, Report, Sample, check_threshold, percentile


def _report(*samples: Sample) -> Report:
    return Report(samples=list(samples))


def test_最近秩法不造出没人经历过的延迟() -> None:
    """插值法会算出 2.5s 这种谁都没遇到过的数。样本少的时候，
    报一个真实发生过的值更可信。"""
    values = [1.0, 2.0, 3.0, 4.0]

    assert percentile(values, 0.50) == 2.0
    assert percentile(values, 0.95) == 4.0
    assert percentile(values, 1.00) == 4.0


def test_单样本时各分位都是它自己() -> None:
    assert percentile([1.7], 0.95) == 1.7


def test_没等到台词的轮次不计入延迟但计入分母() -> None:
    """失败的轮次要是被悄悄丢掉，服务越是崩，延迟报告越好看。"""
    report = _report(
        Sample(first_sentence=1.0),
        Sample(first_sentence=None, error="upstream_unavailable"),
        Sample(first_sentence=2.0),
    )

    assert report.latencies == [1.0, 2.0]
    assert len(report.samples) == 3
    assert report.errors == {"upstream_unavailable": 1}


def test_延迟达标时门槛通过() -> None:
    report = _report(*(Sample(first_sentence=1.2) for _ in range(20)))

    assert check_threshold(report) == []


def test_P95_超线时门槛不通过() -> None:
    # 18 条 1s + 2 条 9s：均值只有 1.8s，P95 却是 9s。
    # 用均值守这条门槛是守不住的，这条用例就是钉住这件事。
    report = _report(
        *(Sample(first_sentence=1.0) for _ in range(18)),
        Sample(first_sentence=9.0),
        Sample(first_sentence=9.0),
    )

    failures = check_threshold(report)
    assert len(failures) == 1
    assert "P95" in failures[0]


def test_二十局里只慢一局不算超线() -> None:
    """P95 的定义就是允许最慢的 5% 出格。少了这条，
    上面那条用例会诱人把门槛改成"任何一条超 3s 就算挂"。"""
    report = _report(
        *(Sample(first_sentence=1.0) for _ in range(19)),
        Sample(first_sentence=9.0),
    )

    assert check_threshold(report) == []


def test_一个有效样本都没有时判定为不通过() -> None:
    """量不出来不等于达标。服务没起、网关不通，都会走到这里。"""
    report = _report(Sample(first_sentence=None, error="network"))

    failures = check_threshold(report)
    assert len(failures) == 1
    assert "无法判定" in failures[0]


def test_门槛取自规格里的三秒() -> None:
    assert P95_CEILING_SECONDS == 3.0


def test_降级轮次单独计数不算失败() -> None:
    """降级是设计好的行为（§6.2）：走了兜底台词的那一轮，
    台词照样第一时间下发，延迟照样算数。"""
    report = _report(
        Sample(first_sentence=0.2, degraded=True),
        Sample(first_sentence=1.5),
    )

    assert report.degraded == 1
    assert report.latencies == [0.2, 1.5]
    assert check_threshold(report) == []
