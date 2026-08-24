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
#
# **v3（2026-08-17）一次补了两样，它们都必须随令牌走：**
#
# · `sid` 场景标识。多场景之后，第二局要是没带它，第 2 轮会被当成老陈那一局
#   重新派生——效力矩阵、兜底台词、结局文案全部换人，而玩家什么都没做。
# · `breaches` 这一局踩过几次合规红线。这个字段第 2 步就加进 GameState 了，
#   **却一直没进令牌**，于是每一轮都从 0 重新开始数。复盘那张合规卡是前端
#   自己按 hits 数的，所以没被发现——一个"存在但从来没有真正生效"的字段。
#
# **v4（2026-08-23）加的是开局上下文**（app/trigger.py）。它必须随令牌走，
# 理由和 `sid` 完全一样：服务端不存任何东西，而每一轮的统计与语料都要能
# 关联回**是哪条异动触发了这次干预、这一局属于哪个实验组**。
# 少了它，业务事件就只能记到"有人打了一局"这个粒度，
# 而干预组与对照组的对比正是这次转向要算的第一个数。
TOKEN_VERSION = 4

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
class Origin:
    """这一局是被什么触发的，属于哪个实验组。

    是 `trigger.StartContext` 的**投影**，不是它本身：只带上关联与分组要用的
    那几个字段。`transaction_ref`（真实交易号）刻意不进令牌——
    令牌是客户端持有的，而交易号是可以拿去别处用的东西，
    它只该活在服务端的事件表里。

    这也是 P1-14 那条"客户端只持有不可推导隐藏信息的会话标识"的边界所在：
    这里的每一项都是**关于用户自己的**，泄漏给他自己的浏览器不构成信息优势。
    """

    anomaly_id: str = ""
    subject_ref: str = ""
    arm: str = ""
    source: str = ""
    trigger_type: str = ""

    def to_wire(self) -> dict:
        return {
            "a": self.anomaly_id,
            "s": self.subject_ref,
            "m": self.arm,
            "o": self.source,
            "t": self.trigger_type,
        }

    @classmethod
    def from_wire(cls, data: dict) -> "Origin":
        if not isinstance(data, dict):
            return cls()
        return cls(
            anomaly_id=data.get("a", ""),
            subject_ref=data.get("s", ""),
            arm=data.get("m", ""),
            source=data.get("o", ""),
            trigger_type=data.get("t", ""),
        )


@dataclass(frozen=True)
class Session:
    gid: str
    state: GameState
    # 场景标识（app/scenario.py）。空串落到默认场景，见 `scenario_for`
    sid: str = ""
    # 复盘面板的唯一数据源。随令牌回到客户端，服务端不需要任何额外存储。
    history: Tuple[TurnRecord, ...] = ()
    # 开场白。它是第 1 轮唯一可供"扎根"的对话内容，因此必须随令牌带着走，
    # 否则玩家开局说得再贴切也会被判成未扎根。
    opening: str = ""
    # 开局上下文（v4）。见 `Origin`。
    origin: Origin = Origin()


def new_session(
    gid: str, opening: str = "", sid: str = "", origin: Origin = Origin()
) -> Session:
    return Session(
        gid=gid, state=new_game(), opening=opening, sid=sid, origin=origin
    )


def sign_session(session: Session, *, secret: str, issued_at: int) -> str:
    payload = _encode(
        {
            "v": TOKEN_VERSION,
            "gid": session.gid,
            "sid": session.sid,
            "org": session.origin.to_wire(),
            "round": session.state.round,
            "trust": session.state.trust,
            "pool": session.state.pool,
            "used": dict(session.state.used),
            "guard": session.state.guard,
            "window": session.state.window,
            "peak": session.state.peak,
            "breaches": session.state.breaches,
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
        sid=data.get("sid", ""),
        state=GameState(
            round=data["round"],
            trust=data["trust"],
            pool=data["pool"],
            used=data["used"],
            guard=data["guard"],
            window=data["window"],
            peak=data["peak"],
            breaches=data["breaches"],
        ),
        history=tuple(TurnRecord.from_wire(r) for r in data.get("history", ())),
        opening=data.get("op", ""),
        origin=Origin.from_wire(data.get("org", {})),
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
