"""状态令牌。

对局状态由客户端持有（ADR-0003），服务端不存会话——所以签名是唯一的防线：
玩家可以看见自己的信任度，但改不动它。

规格见 docs/TECH-DESIGN.md §7.2。
"""

import base64
import json

import pytest

from app.state_token import InvalidStateToken, new_session, sign_session, verify_token

SECRET = "test-secret-not-a-real-key"
NOW = 1788000000


def test_签名后能原样解回() -> None:
    session = new_session(gid="01JTESTGID")

    token = sign_session(session, secret=SECRET, issued_at=NOW)

    assert verify_token(token, secret=SECRET, now=NOW) == session


def test_开场白随令牌往返() -> None:
    """开场白是第 1 轮唯一可供扎根的内容，丢了它这一轮就判不准。"""
    session = new_session(gid="01JTESTGID", opening="有事快说，老师在群里催了。")

    token = sign_session(session, secret=SECRET, issued_at=NOW)

    assert verify_token(token, secret=SECRET, now=NOW).opening == session.opening


def test_换一把密钥无法通过校验() -> None:
    token = sign_session(new_session(gid="01JTESTGID"), secret=SECRET, issued_at=NOW)

    with pytest.raises(InvalidStateToken):
        verify_token(token, secret="another-secret", now=NOW)


def test_篡改信任度会被拒绝() -> None:
    """玩家看得见自己的信任度，但改不动它。"""
    token = sign_session(new_session(gid="01JTESTGID"), secret=SECRET, issued_at=NOW)
    payload, _, signature = token.partition(".")

    # 按 §7.2 的线上格式自行改写载荷，保留原签名
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data["trust"] = 99
    forged = base64.urlsafe_b64encode(
        json.dumps(data, separators=(",", ":")).encode()
    ).decode().rstrip("=")

    with pytest.raises(InvalidStateToken):
        verify_token(f"{forged}.{signature}", secret=SECRET, now=NOW)


def test_令牌超过有效期会被拒绝() -> None:
    token = sign_session(new_session(gid="01JTESTGID"), secret=SECRET, issued_at=NOW)

    with pytest.raises(InvalidStateToken):
        verify_token(token, secret=SECRET, now=NOW + 2 * 3600 + 1)


def test_有效期内的令牌仍然可用() -> None:
    token = sign_session(new_session(gid="01JTESTGID"), secret=SECRET, issued_at=NOW)

    session = verify_token(token, secret=SECRET, now=NOW + 2 * 3600 - 1)

    assert session.gid == "01JTESTGID"
