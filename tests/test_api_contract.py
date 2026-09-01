"""2026-08-23 复核带来的那几条 HTTP 契约变化。

放在 `test_api.py` 之外，因为它们不是"事件顺序与响应头"那一类契约，
而是**这次复核每一条结论在接口上的落点**——一条一条对得上，
将来谁改回去都会在这里红。

| 用例                     | 对应问题 |
|--------------------------|----------|
| 开局响应不含隐藏线索      | P1-14    |
| 开局接受并校验异动上下文  | P0-1     |
| 断流重试不重复记账        | P1-7     |
| 存活与就绪分开            | P1-13    |
| probe 不对公网开放        | P0-6     |
| 退出留痕                  | P1-1     |
"""

import json
from typing import AsyncIterator, List, Tuple

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_gateway


class FakeGateway:
    async def act(self, **kwargs: object) -> AsyncIterator[str]:
        yield "别劝我。"

    async def classify(self, **kwargs: object) -> str:
        return '{"hit_keys": ["socratic_question"], "grounded": true}'

    async def narrate_ending(self, **kwargs: object) -> str:
        return "……你让我再想想。"


@pytest.fixture()
def client() -> TestClient:
    app.dependency_overrides[get_gateway] = lambda: FakeGateway()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _settings(*, offline: bool, configured: bool):
    """造一份改过的 Settings。

    **`Settings` 是 frozen dataclass，monkeypatch 改不动它的字段**
    （`FrozenInstanceError`，而且连 undo 都做不了）。所以整份换掉：
    `dataclasses.replace` 产出一个新实例，再把 `app.main.settings`
    这个模块全局指过去——那是个普通的名字绑定，改得动也还得回来。
    """
    import dataclasses

    from app.config import settings as real

    llm = dataclasses.replace(real.llm, base_url="" if not configured else "https://x",
                              api_key="" if not configured else "k",
                              model="" if not configured else "m")
    return dataclasses.replace(real, llm=llm, offline_demo=offline)


def _events(body: str) -> List[Tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if name:
            out.append((name, data))
    return out


class Test隐藏线索不在开局下发:
    """P1-14。他手机上那几条是这一局要挖的答案；开局响应里带着它，
    打开开发者工具就能提前看完。

    比赛里这是公平问题；接进真实业务之后这是**实验数据可信度**问题——
    一批"看过答案的对局"会把干预效果算高，而没有任何字段能把它们标出来。
    """

    def test_开局响应里没有phone(self, client: TestClient) -> None:
        scenario = client.post("/api/game/start").json()["scenario"]
        assert "phone" not in scenario

    def test_整个开局响应里搜不到线索文案(self, client: TestClient) -> None:
        """不只看字段名——线索可能被塞进别的字段里。"""
        blob = client.post("/api/game/start", json={"sid": "chen"}).text
        for leak in ("clue", "反诈中心", "启航财经"):
            assert leak not in blob, f"开局响应里泄露了 {leak!r}"

    def test_结局那一屏才给(self, client: TestClient) -> None:
        """对正常玩家没有任何差别：他本来也是打完才看见的。"""
        from app.config import settings
        from app.scoring import MAX_ROUNDS, GameState
        from app.state_token import Session, sign_session
        import time

        session = Session(
            gid="reveal-test", sid="chen",
            state=GameState(round=MAX_ROUNDS - 1, trust=50, pool=0, used={},
                            guard=0, window=0, peak=50, breaches=0),
        )
        token = sign_session(
            session, secret=settings.state_signing_secret, issued_at=int(time.time())
        )
        resp = client.post("/api/game/turn", json={"token": token, "utterance": "最后一句"})
        ending = dict(_events(resp.text)).get("ending")
        assert ending is not None, "第 12 轮该出结局"
        assert ending["phone"], "揭晓清单要在这一刻下发"
        assert ending["phone"][0]["clue"]


class Test开局上下文:
    """P0-1。场景由真实异动类型在服务端选，不由随机 gid 派生。"""

    def test_不传上下文仍然能开局且标成演示(self, client: TestClient) -> None:
        body = client.post("/api/game/start").json()
        assert body["origin"]["source"] == "demo"
        assert body["origin"]["trigger_type"]
        assert body["origin"]["arm"]

    def test_传了真实上下文就按它选场景(self, client: TestClient) -> None:
        body = client.post("/api/game/start", json={
            "anomaly_id": "AN-1", "trigger_type": "fund_redemption",
            "subject_ref": "u_1", "transaction_ref": "TX-1",
        }).json()
        assert body["origin"]["source"] == "upstream"
        assert body["scenario"]["id"] in ("zhou", "liu"), "赎回该落到赎回类剧本"

    def test_非法上下文返回400而不是照常开局(self, client: TestClient) -> None:
        resp = client.post("/api/game/start", json={
            "anomaly_id": "AN-1", "trigger_type": "不存在的类型", "subject_ref": "u",
        })
        assert resp.status_code == 400
        assert resp.json()["code"] == "bad_context"

    def test_真实接入时不给客户清单(self, client: TestClient) -> None:
        """真实接入时前端不该出现"换一位客户"——场景由用户自己那笔异动决定，
        让他挑等于让他挑一个和自己处境无关的剧本。"""
        body = client.post("/api/game/start", json={
            "anomaly_id": "AN-2", "trigger_type": "full_liquidation",
            "subject_ref": "u_2",
        }).json()
        assert body["catalog"] == []

    def test_演示态给客户清单(self, client: TestClient) -> None:
        assert len(client.post("/api/game/start").json()["catalog"]) == 5


class Test断流重试不重复记账:
    """P1-7。统计与语料在 `score` 那一刻就写下去，而令牌要整轮走完才标记。
    客户端在两者之间断开并重试，同一个 `(gid, round)` 会被写第二遍。

    修法是给副作用单独一把锁（`turn:<gid>:<round>`），
    **而不是把令牌提前标记掉**——那会让网关抖一下就作废玩家刚说的那句话。
    """

    def test_同一轮重复请求只记一次账(self, client: TestClient, monkeypatch) -> None:
        recorded: List[tuple] = []
        from app import main

        monkeypatch.setattr(
            main.stats, "record_turn",
            lambda hits, **kw: recorded.append(("turn", tuple(hits))),
        )

        start = client.post("/api/game/start").json()
        token = start["token"]

        first = client.post("/api/game/turn", json={"token": token, "utterance": "第一句"})
        assert dict(_events(first.text)).get("score") is not None
        assert len(recorded) == 1

        # 同一张令牌再来一次：重放防护会先挡下来（这是正常路径）
        again = client.post("/api/game/turn", json={"token": token, "utterance": "第一句"})
        assert dict(_events(again.text)).get("error", {}).get("code") == "replayed"
        assert len(recorded) == 1, "被挡下的重放不该产生第二笔账"

    def test_没走完的那一轮不消费令牌(self, client: TestClient, monkeypatch) -> None:
        """**这一条是"进门只读"存在的全部理由。**

        进门就把令牌标成用过的话，这一轮真的没走完时，
        玩家刚说的那句话就永远发不出去了。

        注意这里模拟的**不是网关抖动**：网关抖动由引擎自己降级吸收
        （L1 兜底台词 + L2 中性判分），那一轮仍然算走完了，令牌该消费。
        要模拟的是"整轮没走完"——编排层自己炸了、或者客户端在中途断开。
        """
        from app import main

        boom = {"on": True}
        real = main.play_turn

        def fake_play_turn(*a, **kw):
            async def gen():
                if boom["on"]:
                    raise RuntimeError("编排层炸了")
                async for e in real(*a, **kw):
                    yield e
            return gen()

        monkeypatch.setattr(main, "play_turn", fake_play_turn)

        token = client.post("/api/game/start").json()["token"]
        first = client.post("/api/game/turn", json={"token": token, "utterance": "会失败的一句"})
        assert dict(_events(first.text))["error"]["code"] == "internal"

        # 恢复，同一张令牌重试
        boom["on"] = False
        retry = client.post("/api/game/turn", json={"token": token, "utterance": "重试这一句"})
        codes = [d.get("code") for n, d in _events(retry.text) if n == "error"]
        assert "replayed" not in codes, "没走完的那一张令牌必须还能重试"
        assert dict(_events(retry.text)).get("score") is not None

    def test_降级但走完的那一轮照常消费令牌(self, client: TestClient) -> None:
        """反过来的一半：网关抖动被降级吸收之后，**那一轮算走完了**。

        兜底台词已经播出去了、判分已经下发了，让它还能重来，
        等于给玩家一个"说砸了就撤销"的入口——而本作唯一在判的就是时机。
        """
        class Flaky(FakeGateway):
            async def act(self, **kwargs: object) -> AsyncIterator[str]:
                raise RuntimeError("网关抖了")
                yield  # pragma: no cover

            async def classify(self, **kwargs: object) -> str:
                raise RuntimeError("网关抖了")

        token = client.post("/api/game/start").json()["token"]
        app.dependency_overrides[get_gateway] = lambda: Flaky()
        first = client.post("/api/game/turn", json={"token": token, "utterance": "一句话"})
        score = dict(_events(first.text)).get("score")
        assert score is not None and score["degraded"] is True

        app.dependency_overrides[get_gateway] = lambda: FakeGateway()
        again = client.post("/api/game/turn", json={"token": token, "utterance": "再来一次"})
        assert dict(_events(again.text)).get("error", {}).get("code") == "replayed"


class Test存活与就绪分开:
    """P1-13。原先只有一个 `/healthz`，而且**大模型没配的时候它照样返回
    `status: ok`**——一台完全无法完成一局对局的服务，在负载均衡眼里是健康的。
    """

    def test_healthz永远是200(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readyz报出版本与配置摘要(self, client: TestClient) -> None:
        body = client.get("/readyz").json()
        assert set(body["versions"]) == {
            "app", "rules", "prompt", "scenario", "classifier"
        }
        assert "api_key" not in json.dumps(body)

    def test_模型没配时readyz是503(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr("app.main.settings", _settings(offline=False, configured=False))
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["ready"] is False
        assert resp.json()["reason"]

    def test_离线演示模式下是就绪的(self, client: TestClient, monkeypatch) -> None:
        """它压根不调网关，而且判分是纯函数（ADR-0001），一格都不打折。"""
        monkeypatch.setattr("app.main.settings", _settings(offline=True, configured=False))
        assert client.get("/readyz").status_code == 200

    def test_healthz报出是否已自动切到公网(self, client: TestClient) -> None:
        """ADR-0007。反对自动切换的原始理由是"无人知情"——自动切换本身
        没错，悄悄切才是问题，所以这一位必须在 healthz 上，不能只在日志里。

        `fallback` 是 2026-08-29 加的第五栏，回答的是另一个问题：
        **不是"切没切过"，是"要切的时候有没有地方切"**。没有退路时它是
        None，而那种部署在内网 token 用尽当天会安静地全程走兜底台词。
        """
        body = client.get("/healthz").json()
        failover = body["llm_failover"]
        assert set(failover) == {
            "active_provider", "active_model", "switched", "switched_at",
            "reason", "fallback",
        }
        assert isinstance(failover["switched"], bool)
        # `active_model` 是 2026-08-31 加的第六栏。**`active_provider` 回答不了
        # 部署当天真正要确认的那件事**——它只说"内网还是公网"，而运维要问的是
        # "这台服务到底在用 claude-opus-5 吗"。切换之后这一位跟着变，
        # 而启动日志里那个模型名不会变，两者一对就知道切没切过。
        assert failover["active_model"] == body["llm_model"] or failover["switched"]
        # 有没有退路取决于运维填没填 PUBLIC_LLM_*，两种都是合法部署；
        # 契约钉的是"这一位必须有明确答案"，不是"必须有退路"
        assert failover["fallback"] is None or set(failover["fallback"]) == {
            "provider", "model", "protocol",
        }

    def test_healthz报出台词是谁写的(self, client: TestClient) -> None:
        """2026-08-31 加。**`/healthz` 原有的每一位说的都是"启动那一刻"的事**：
        网关配没配、探测通不通、切没切过。而探测成功之后网关照样可能每一轮
        都超时，玩家拿到的每一句都是兜底台词——这台服务在"用大模型"这件事上
        名存实亡，`/healthz` 却一路绿。

        兜底台词是**故意**写得让人察觉不出来的（`fallback.py` 顶部那句
        "玩家未必察觉"），所以它同样骗得过运维。这三个数把它变成一个
        看得见的比值：`fallback` 一直在涨就是在发罐头。
        """
        body = client.get("/healthz").json()
        assert set(body["line_sources"]) == {
            "model", "fallback", "absorbed", "safety_escalation",
        }
        assert all(isinstance(v, int) for v in body["line_sources"].values())
        # 配置里那个模型名。和 `llm_failover.active_model` 一起看才回答得了
        # "现在到底在用哪个"——切换之后这一位不变，那一位会变
        assert "llm_model" in body


class Test探测不对公网开放:
    """P0-6。`?probe=1` 会让服务**从公网请求触发一次对内网网关的出站调用**，
    并把网关地址、状态码回给调用方。拿到服务器当天这是必要的排查动作，
    但它不该是一个任何人都能打的公开接口。
    """

    def test_本机可以探测(self, monkeypatch) -> None:
        """运维 ssh 上去 `curl localhost` 那条路必须留着——
        那是拿到服务器当天最先要跑的一件事。"""
        from app import main

        async def _probe():
            return {"ok": True, "provider": "internal"}

        monkeypatch.setattr(main.llm_client, "probe", _probe)
        # TestClient 默认的 client host 是 "testclient"，不是 127.0.0.1，
        # 不显式指定的话这条用例测的是"陌生来源"，跟下一条就重复了
        with TestClient(app, client=("127.0.0.1", 51234)) as c:
            body = c.get("/healthz?probe=1").json()
        assert body["llm_probe"]["ok"] is True

    def test_非本机且没令牌时拒绝(self, monkeypatch) -> None:
        from app import main

        called = []

        async def _probe():
            called.append(1)
            return {"ok": True}

        monkeypatch.setattr(main.llm_client, "probe", _probe)
        monkeypatch.delenv("HEALTH_PROBE_TOKEN", raising=False)

        app.dependency_overrides[get_gateway] = lambda: FakeGateway()
        with TestClient(app, client=("203.0.113.9", 51234)) as c:
            body = c.get("/healthz?probe=1").json()
        app.dependency_overrides.clear()

        assert body["llm_probe"]["ok"] is False
        assert called == [], "拒绝之后不该真的去打网关"

    def test_带对得上的令牌可以探测(self, monkeypatch) -> None:
        from app import main

        async def _probe():
            return {"ok": True}

        monkeypatch.setattr(main.llm_client, "probe", _probe)
        monkeypatch.setenv("HEALTH_PROBE_TOKEN", "s3cret")

        with TestClient(app, client=("203.0.113.9", 51234)) as c:
            body = c.get("/healthz?probe=1", headers={"X-Probe-Token": "s3cret"}).json()
        assert body["llm_probe"]["ok"] is True


class Test退出留痕:
    """P1-1。退出必须留痕，理由不是监控用户，是**护栏指标**：
    直接关闭率、中断率是判断这个干预有没有伤到用户体验的第一组数。
    """

    def test_退出会被记一笔(self, client: TestClient, monkeypatch) -> None:
        from app import main

        seen = []
        monkeypatch.setattr(
            main.stats, "record_exit",
            lambda reason, **kw: seen.append((reason, kw.get("rounds"))),
        )
        token = client.post("/api/game/start").json()["token"]
        resp = client.post("/api/game/exit", json={"token": token, "reason": "dismissed"})
        assert resp.json() == {"ok": True, "recorded": True}
        assert seen == [("dismissed", 0)]

    def test_一局只记一次(self, client: TestClient, monkeypatch) -> None:
        """用户可能连点两下，或者退出后回来又退出。"""
        from app import main

        seen = []
        monkeypatch.setattr(main.stats, "record_exit", lambda r, **k: seen.append(r))
        token = client.post("/api/game/start").json()["token"]
        client.post("/api/game/exit", json={"token": token, "reason": "abandoned"})
        second = client.post("/api/game/exit", json={"token": token, "reason": "abandoned"})
        assert second.json()["recorded"] is False
        assert len(seen) == 1

    def test_令牌过期也不许挡住退出(self, client: TestClient) -> None:
        """**绝不能让埋点挡住退出。** 记不下来就记不下来，用户该走还是能走。"""
        resp = client.post("/api/game/exit", json={"token": "garbage", "reason": "abandoned"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["recorded"] is False

    def test_未知理由归到中断而不是报错(self, client: TestClient, monkeypatch) -> None:
        from app import main

        seen = []
        monkeypatch.setattr(main.stats, "record_exit", lambda r, **k: seen.append(r))
        token = client.post("/api/game/start").json()["token"]
        client.post("/api/game/exit", json={"token": token, "reason": "whatever"})
        assert seen == ["abandoned"]


class Test连续故障时停止发放新干预:
    """P1-13。已经在打的那一局照常降级走完，但不再发放新的。"""

    def test_熔断时开局返回503(self, client: TestClient, monkeypatch) -> None:
        from app.guard import Breaker

        monkeypatch.setattr("app.main.breaker", Breaker(threshold=1))
        monkeypatch.setattr("app.main.settings", _settings(offline=False, configured=True))
        from app import main

        main.breaker.record(ok=False)
        resp = client.post("/api/game/start")
        assert resp.status_code == 503
        assert resp.json()["code"] == "unavailable"
        assert resp.headers.get("Retry-After")

    def test_离线演示模式不受熔断影响(self, client: TestClient, monkeypatch) -> None:
        """它压根不调网关，判分是纯函数（ADR-0001），一格都不打折。"""
        from app.guard import Breaker

        b = Breaker(threshold=1)
        b.record(ok=False)
        monkeypatch.setattr("app.main.breaker", b)
        monkeypatch.setattr("app.main.settings", _settings(offline=True, configured=False))
        assert client.post("/api/game/start").status_code == 200

    def test_没熔断时照常开局(self, client: TestClient) -> None:
        assert client.post("/api/game/start").status_code == 200
