"""终局封口（P1-9）。

引擎原先直接 `round = state.round + 1`，没有任何一处问过"这一局是不是已经
打完了"。于是拿终局令牌再发一次请求，会进第 13 轮、`remaining` 返回 −1，
终局与统计被重复写一遍，前端与服务端对"这一局有几轮"的认识就此分叉。

**两层都要拦，而且判据只有一份**（`scoring.is_finished`）：
- HTTP 层拦住的请求**一个模型请求都不发**，刷接口的人连网关额度都吃不到；
- 引擎层保护的是直接调 `play_turn` 的其他入口（跑批、蒙特卡洛、将来的接入）。
"""

import time
from typing import AsyncIterator, List

import pytest
from fastapi.testclient import TestClient

from app.engine import SessionFinished, play_turn
from app.main import app, get_gateway
from app.scoring import (
    BLACKLIST_THRESHOLD,
    MAX_ROUNDS,
    WIN_THRESHOLD,
    GameState,
    is_finished,
    new_game,
)
from app.state_token import Session, sign_session

SECRET = "test-secret"


class FakeGateway:
    async def act(self, **kwargs: object) -> AsyncIterator[str]:
        yield "随便说点什么。"

    async def classify(self, **kwargs: object) -> str:
        return '{"hit_keys": [], "grounded": false}'

    async def narrate_ending(self, **kwargs: object) -> str:
        return "……行吧。"


def _state(**over) -> GameState:
    base = dict(round=0, trust=32, pool=0, used={}, guard=0, window=0,
                peak=32, breaches=0)
    base.update(over)
    return GameState(**base)


class Test判据只有一份:
    """`is_finished` 与 `decide_ending` 必须是同一个判据。

    两份判据会慢慢走散：引擎认为还能打、HTTP 认为不能，或者反过来。
    """

    def test_没打完的局不算结束(self) -> None:
        assert is_finished(_state(round=5, trust=50)) is False

    def test_打满全场算结束(self) -> None:
        assert is_finished(_state(round=MAX_ROUNDS, trust=50)) is True

    def test_过了劝住线算结束(self) -> None:
        assert is_finished(_state(round=4, trust=WIN_THRESHOLD)) is True

    def test_掉到拉黑线算结束(self) -> None:
        assert is_finished(_state(round=4, trust=BLACKLIST_THRESHOLD)) is True

    def test_新开的局不算结束(self) -> None:
        assert is_finished(new_game()) is False


class Test引擎层:
    async def test_终局令牌不许再推进(self) -> None:
        session = Session(gid="g", sid="chen", state=_state(round=MAX_ROUNDS, trust=50))
        with pytest.raises(SessionFinished):
            async for _ in play_turn(
                session, "还想再说一句", gateway=FakeGateway(),
                secret=SECRET, now=int(time.time()),
            ):
                pass

    async def test_拦在任何副作用之前(self) -> None:
        """**一个模型请求都不许发。**

        分类和演绎都在 `yield` 之前就发车了，判据要是放在后面，
        一张过期令牌照样能吃掉网关额度。
        """
        called: List[str] = []

        class Spy(FakeGateway):
            async def classify(self, **kwargs: object) -> str:
                called.append("classify")
                return "{}"

            async def act(self, **kwargs: object) -> AsyncIterator[str]:
                called.append("act")
                yield "x"

        session = Session(gid="g", sid="chen", state=_state(round=4, trust=WIN_THRESHOLD))
        with pytest.raises(SessionFinished):
            async for _ in play_turn(
                session, "x", gateway=Spy(), secret=SECRET, now=int(time.time()),
            ):
                pass
        assert called == [], f"终局令牌不该触发任何网关调用，实际: {called}"

    async def test_没打完的局照常推进(self) -> None:
        """封口不能把正常对局也封掉。"""
        session = Session(gid="g", sid="chen", state=_state(round=3, trust=50))
        names = [
            e.name
            async for e in play_turn(
                session, "这钱本来干什么用的", gateway=FakeGateway(),
                secret=SECRET, now=int(time.time()),
            )
        ]
        assert "score" in names and "done" in names


class TestHTTP层:
    def _client(self) -> TestClient:
        app.dependency_overrides[get_gateway] = lambda: FakeGateway()
        return TestClient(app)

    def teardown_method(self) -> None:
        app.dependency_overrides.clear()

    def _finished_token(self) -> str:
        from app.config import settings

        session = Session(
            gid="finished-game", sid="chen",
            state=_state(round=MAX_ROUNDS, trust=50),
        )
        return sign_session(
            session, secret=settings.state_signing_secret, issued_at=int(time.time())
        )

    def test_终局令牌返回finished而不是internal(self) -> None:
        """**不能落进 internal。**

        那会把一个状态机约束伪装成服务端故障：前端会提示"出了点岔子，
        再说一次试试"，而再说一次仍然会被拒——玩家陷在一个死循环里。
        """
        resp = self._client().post(
            "/api/game/turn",
            json={"token": self._finished_token(), "utterance": "第十三轮"},
        )
        assert resp.status_code == 200
        assert '"code": "finished"' in resp.text or '"code":"finished"' in resp.text
        assert "internal" not in resp.text

    def test_不会出现第13轮或负数剩余(self) -> None:
        """这是复核当时实测到的那个现象本身。"""
        resp = self._client().post(
            "/api/game/turn",
            json={"token": self._finished_token(), "utterance": "第十三轮"},
        )
        assert '"round": 13' not in resp.text
        assert "remaining" not in resp.text or '"remaining": -1' not in resp.text
