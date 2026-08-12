"""状态令牌：客户端持有的对局状态 + HMAC 签名。

服务端不存会话（ADR-0003），因此签名是唯一的防线——玩家看得见自己的信任度，
但改不动它。history 同时是复盘面板的唯一数据源，复盘不需要额外存储。

传输形态：base64url(json) + "." + hmac_sha256(SECRET, payload)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Tuple

from .scoring import GameState, new_game

# v2：GameState 增加 guard / window / peak。旧令牌一律拒绝而不做兼容解析——
# 缺字段的局按默认值续玩会算出与判分规格不符的分数，那比让玩家重开一局更糟。
TOKEN_VERSION = 2

# 令牌有效期。签名本身不防重放，过期时间是那道兜底：
# 一个泄漏的令牌最多只能被拿来续玩两小时。
TOKEN_TTL_SECONDS = 2 * 3600


class InvalidStateToken(Exception):
    """签名不符、载荷损坏或已过期。一律拒绝，不做任何补救性解析。"""


@dataclass(frozen=True)
class TurnRecord:
    """一轮的完整记录。字段在令牌里用短键存放，令牌体积直接影响每轮请求大小。"""

    round: int
    utterance: str  # 玩家发言
    reply: str      # 劝阻对象的台词
    hits: Tuple[str, ...]
    grounded: bool
    delta: int

    def to_wire(self) -> dict:
        return {
            "r": self.round,
            "u": self.utterance,
            "a": self.reply,
            "h": list(self.hits),
            "g": self.grounded,
            "d": self.delta,
        }

    @classmethod
    def from_wire(cls, data: dict) -> "TurnRecord":
        return cls(
            round=data["r"],
            utterance=data["u"],
            reply=data["a"],
            hits=tuple(data["h"]),
            grounded=data["g"],
            delta=data["d"],
        )


@dataclass(frozen=True)
class Session:
    gid: str
    state: GameState
    # 复盘面板的唯一数据源。随令牌回到客户端，服务端不需要任何额外存储。
    history: Tuple[TurnRecord, ...] = ()
    # 开场白。它是第 1 轮唯一可供"扎根"的对话内容，因此必须随令牌带着走，
    # 否则玩家开局说得再贴切也会被判成未扎根。
    opening: str = ""


def new_session(gid: str, opening: str = "") -> Session:
    return Session(gid=gid, state=new_game(), opening=opening)


def sign_session(session: Session, *, secret: str, issued_at: int) -> str:
    payload = _encode(
        {
            "v": TOKEN_VERSION,
            "gid": session.gid,
            "round": session.state.round,
            "trust": session.state.trust,
            "pool": session.state.pool,
            "used": dict(session.state.used),
            "guard": session.state.guard,
            "window": session.state.window,
            "peak": session.state.peak,
            "history": [record.to_wire() for record in session.history],
            "op": session.opening,
            "iat": issued_at,
        }
    )
    return f"{payload}.{_signature(payload, secret)}"


def verify_token(token: str, *, secret: str, now: int) -> Session:
    payload, _, signature = token.partition(".")
    if not payload or not signature:
        raise InvalidStateToken("令牌格式不合法")

    # 定长比较，避免时序侧信道
    if not hmac.compare_digest(signature, _signature(payload, secret)):
        raise InvalidStateToken("签名不符")

    data = _decode(payload)
    if data.get("v") != TOKEN_VERSION:
        raise InvalidStateToken(f"令牌版本不受支持: {data.get('v')!r}")
    if now - int(data["iat"]) > TOKEN_TTL_SECONDS:
        raise InvalidStateToken("令牌已过期")

    return Session(
        gid=data["gid"],
        state=GameState(
            round=data["round"],
            trust=data["trust"],
            pool=data["pool"],
            used=data["used"],
            guard=data["guard"],
            window=data["window"],
            peak=data["peak"],
        ),
        history=tuple(TurnRecord.from_wire(r) for r in data.get("history", ())),
        opening=data.get("op", ""),
    )


def _signature(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _encode(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(payload: str) -> dict:
    padding = "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload + padding))
    except (ValueError, TypeError) as exc:
        raise InvalidStateToken(f"载荷无法解析: {exc}") from exc
