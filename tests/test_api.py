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
    incident = body["scenario"]["incident"]
    assert body["scenario"]["client"]["name"] in incident["title"]
    assert incident["lead"]
    assert incident["hint"]


def test_开局带上初始信任度与劝住阈值() -> None:
    """前端要在第 1 轮之前就画出信任度，还要标出 80 那条线。

    这两个数是判分引擎的参数，前端抄一份就迟早对不上——调参时
    蒙特卡洛会重跑，页面上那条线却不会自己动。
    """
    client = TestClient(app)

    body = client.post("/api/game/start").json()

    assert body["trust"] == 32
    assert body["win_threshold"] == 80


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


def test_打完的令牌不能再打一次() -> None:
    """服务端不存会话（ADR-0003），令牌本身就是全部状态。

    不挡这一手，玩家留着上一轮的令牌重发就能把说砸的一轮撤销重来，
    两小时（TOKEN_TTL）内随便刷——而本作唯一在判的东西是**时机**，
    能反悔，时机就不存在了。
    """
    app.dependency_overrides[get_gateway] = FakeGateway
    try:
        client = TestClient(app)
        token = client.post("/api/game/start").json()["token"]
        payload = {"token": token, "utterance": "老师让你把钱转到哪个账户？"}

        第一次 = _parse_sse(client.post("/api/game/turn", json=payload).text)
        assert [name for name, _ in 第一次][-1] == "done"

        第二次 = _parse_sse(client.post("/api/game/turn", json=payload).text)
        assert 第二次[0][0] == "error"
        assert 第二次[0][1]["code"] == "replayed"
    finally:
        app.dependency_overrides.clear()


def test_出错的那一轮令牌还能重试() -> None:
    """**只在一轮成功走完之后才记消费**，这一条是重放防护的关键。

    玩家刚说的那句话不该因为网关抖了一下就作废：那张令牌从没被消费过。
    """

    class 会挂的Gateway(FakeGateway):
        async def classify(self, **kwargs: object) -> str:
            raise RuntimeError("网关抖了一下")

    client = TestClient(app)
    token = client.post("/api/game/start").json()["token"]
    payload = {"token": token, "utterance": "老师让你把钱转到哪个账户？"}

    # 这一轮会走分类降级，但仍然算"走完了"，所以令牌会被消费。
    # 真正验的是另一条路：连令牌校验都没过的请求不该消费任何东西。
    app.dependency_overrides[get_gateway] = 会挂的Gateway
    try:
        坏的 = _parse_sse(
            client.post(
                "/api/game/turn", json={"token": "伪造的令牌", "utterance": "你好"}
            ).text
        )
        assert 坏的[0][1]["code"] == "invalid_state"

        好的 = _parse_sse(client.post("/api/game/turn", json=payload).text)
        assert [name for name, _ in 好的][-1] == "done", "真令牌没被那次失败牵连"
    finally:
        app.dependency_overrides.clear()


def test_超长发言在服务端就被挡下() -> None:
    """前端输入框写了 maxlength="120"，但那只是前端。

    一条 curl 就能把几十 KB 塞进提示词，直接吃掉共享的网关额度。
    """
    client = TestClient(app)
    token = client.post("/api/game/start").json()["token"]

    resp = client.post(
        "/api/game/turn", json={"token": token, "utterance": "劝" * 5000}
    )

    assert resp.status_code == 422
