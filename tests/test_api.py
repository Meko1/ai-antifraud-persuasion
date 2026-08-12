"""HTTP 契约。

这一层只验证契约本身：事件顺序、响应头、错误码。判分与安全层的行为
在各自的接缝上已经验过，不在这里重复。

规格见 docs/TECH-DESIGN.md §7。
"""

import json
from typing import AsyncIterator, List, Tuple

from fastapi.testclient import TestClient

from app.main import app, get_gateway


class FakeGateway:
    async def act(self, **kwargs: object) -> AsyncIterator[str]:
        for ch in "别劝我。老师说了今天最后一天。":
            yield ch

    async def classify(self, **kwargs: object) -> str:
        return '{"hit_keys": ["socratic_question"], "grounded": true}'

    async def narrate_ending(self, **kwargs: object) -> str:
        return "……你让我再想想。"


def _parse_sse(body: str) -> List[Tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:") :].strip())
        if name is not None:
            events.append((name, data))
    return events


def test_开局返回预生成开场白与初始签名状态() -> None:
    """开场白是预生成的，不调模型——「首屏 ≤3 秒」靠的就是这个。"""
    client = TestClient(app)

    resp = client.post("/api/game/start")

    assert resp.status_code == 200
    body = resp.json()
    assert body["opening"], "开场白不能为空"
    assert body["token"], "必须带回初始签名状态"
    assert body["remaining"] == 12


def test_开局带上初始信任度与劝住阈值() -> None:
    """前端要在第 1 轮之前就画出信任度，还要标出 80 那条线。

    这两个数是判分引擎的参数，前端抄一份就迟早对不上——调参时
    蒙特卡洛会重跑，页面上那条线却不会自己动。
    """
    client = TestClient(app)

    body = client.post("/api/game/start").json()

    assert body["trust"] == 32
    assert body["win_threshold"] == 80


def test_开局带上参赛编号供分享卡使用() -> None:
    """分享卡要印参赛编号（§8），它不能硬编码在前端。

    没配置时返回空串，分享卡就不印那一行——空着总好过印一个占位符
    发到社交平台上。
    """
    client = TestClient(app)

    body = client.post("/api/game/start").json()

    assert "contest_id" in body
    assert isinstance(body["contest_id"], str)


def test_一轮对话的SSE事件顺序与防缓冲响应头() -> None:
    app.dependency_overrides[get_gateway] = FakeGateway
    try:
        client = TestClient(app)
        token = client.post("/api/game/start").json()["token"]

        resp = client.post(
            "/api/game/turn",
            json={"token": token, "utterance": "老师让你把钱转到哪个账户？"},
        )

        assert resp.status_code == 200
        # 少了这个头，反向代理会把流式攒成一次性下发，对话卡成弹窗
        assert resp.headers["x-accel-buffering"] == "no"
        assert resp.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(resp.text)
        assert [name for name, _ in events] == [
            "meta",
            "sentence",
            "sentence",
            "score",
            "state",
            "done",
        ]
    finally:
        app.dependency_overrides.clear()


def test_令牌不合法时返回错误事件而不是500() -> None:
    app.dependency_overrides[get_gateway] = FakeGateway
    try:
        client = TestClient(app)

        resp = client.post(
            "/api/game/turn", json={"token": "伪造的令牌", "utterance": "你好"}
        )

        events = _parse_sse(resp.text)
        assert events[0][0] == "error"
        assert events[0][1]["code"] == "invalid_state"
    finally:
        app.dependency_overrides.clear()


def test_统计接口在没配redis时返回不可用而不是报错() -> None:
    """统计是旁路。没配 Redis 时它必须安静地说"没有"，
    而不是 500——一个纯展示接口不该让健康检查看起来像出事了。"""
    client = TestClient(app)

    resp = client.get("/api/stats")

    assert resp.status_code == 200
    assert resp.json() == {"available": False}
