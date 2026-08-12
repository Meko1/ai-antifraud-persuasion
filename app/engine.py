"""对局编排：把一轮对话拆成事件流。

每轮并发两个请求——演绎（流式、长、可降级）与分类（非流式、短、不可降级）。
分类的耗时被台词的流式输出完全掩盖，首字延迟等同于单次调用。

台词故意落后分数一轮（ADR-0002）：玩家说中要害、对方嘴上仍硬、下一轮才松动。
这既是真实劝阻的样子，也是并行架构的前提。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Protocol

import logging

from .classify import Classification, parse_classification
from .fallback import fallback_line
from .safety import absorb_injection, screen_sentence
from .scoring import MAX_ROUNDS, Ending, evaluate_turn, mood_for, under_pressure
from .state_token import Session, TurnRecord, sign_session
from .streaming import SentenceBuffer


class Gateway(Protocol):
    """模型网关端口。三个操作各自独立可替身，调用方不必分辨传输细节。"""

    def act(self, **kwargs: Any) -> AsyncIterator[str]: ...

    async def classify(self, **kwargs: Any) -> str: ...

    async def narrate_ending(self, **kwargs: Any) -> str: ...


logger = logging.getLogger(__name__)

# L1 降级线：演绎超时就改用兜底台词。分类另有更长的预算且不可降级。
ACT_TIMEOUT_SECONDS = 6.0


async def _act_with_deadline(
    gateway: Gateway, *, timeout: float, **kwargs: Any
) -> AsyncIterator[str]:
    """给整段演绎设一个总预算，逐块推进时扣减。

    不能只给整体 await 设超时——那样会退化成非流式，首句延迟就没了。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    iterator = gateway.act(**kwargs).__aiter__()
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), remaining)
        except StopAsyncIteration:
            return
        yield chunk


# 注入吸收命中时的判分：既未命中钥匙也未触发失误，只吃信任流失。
# 它不是"降级"——分数是确定的，degraded 必须保持 false。
_NEUTRAL = Classification(hit_keys=(), grounded=False)


@dataclass(frozen=True)
class Event:
    name: str
    data: Dict[str, Any] = field(default_factory=dict)


def _screened(sentences: Iterable[str]) -> List[str]:
    """过安全层，并丢掉被剥成空壳的句子（整句只是一句舞台指示）。"""
    out = []
    for sentence in sentences:
        text = screen_sentence(sentence)
        if text is not None:
            out.append(text)
    return out


def _split_screened(text: str) -> List[str]:
    """把一整段文本切成句子再过安全层。

    结局台词是非流式拿到的一整段，但它下发时同样一句一个气泡——
    对玩家来说，最后那几句和前面十二轮没有任何区别。
    """
    buffer = SentenceBuffer()
    return _screened(buffer.feed(text) + buffer.flush())


async def play_turn(
    session: Session,
    utterance: str,
    *,
    gateway: Gateway,
    secret: str,
    now: int,
    act_timeout: float = ACT_TIMEOUT_SECONDS,
) -> AsyncIterator[Event]:
    state = session.state
    round_ = state.round + 1

    yield Event("meta", {"round": round_, "remaining": MAX_ROUNDS - round_})

    # 台词逐句下发，同时攒起来写进 history——复盘要靠它
    spoken: List[str] = []

    absorbed = absorb_injection(utterance)
    if absorbed is not None:
        # 装听不懂：请求根本不发给模型，攻击者什么也拿不到。
        # 该轮记 neutral——不加不减，但仍吃信任流失。
        buffer = SentenceBuffer()
        for sentence in buffer.feed(absorbed) + buffer.flush():
            spoken.append(sentence)
            yield Event("sentence", {"text": sentence})
        hit_keys: tuple = ()
        grounded = False
        classification = _NEUTRAL
    else:
        # 分类先发车：它的耗时要被台词的流式输出盖住
        classification_task = asyncio.create_task(
            gateway.classify(
                utterance=utterance,
                history=session.history,
                opening=session.opening,
            )
        )

        # 演绎携带的是【上一轮结束时】的情绪档位——台词落后一轮正是从这里来的
        mood = mood_for(state.trust)
        buffer = SentenceBuffer()
        try:
            async for chunk in _act_with_deadline(
                gateway,
                mood=mood,
                utterance=utterance,
                timeout=act_timeout,
                history=session.history,
                opening=session.opening,
                # 该轮李老师又催了一遍：判分要多扣 3 分，台词也得跟着紧张起来。
                # 隐形的信任流失从这里变成一个玩家看得见的施压来源。
                pressured=under_pressure(round_),
            ):
                for text in _screened(buffer.feed(chunk)):
                    spoken.append(text)
                    yield Event("sentence", {"text": text})
            for text in _screened(buffer.flush()):
                spoken.append(text)
                yield Event("sentence", {"text": text})
        except asyncio.TimeoutError:
            logger.warning("演绎超时（L1 降级），改用兜底台词")

        if not spoken:
            # L1：演绎降级。绝不给玩家一片空白。
            text = fallback_line(mood)
            spoken.append(text)
            yield Event("sentence", {"text": text})

        classification = parse_classification(await classification_task)
        hit_keys = classification.hit_keys if classification else ()
        grounded = classification.grounded if classification else False

    outcome = evaluate_turn(state, hit_keys=hit_keys, grounded=grounded)
    yield Event(
        "score",
        {
            "delta": outcome.delta,
            "trust": outcome.state.trust,
            # 档位随分数一起下发：前端要把「信任度 50」说成「他开始动摇了」，
            # 阈值只此一份，抄到前端去调参时就会走散
            "mood": mood_for(outcome.state.trust).value,
            "hits": list(hit_keys),
            "grounded": grounded,
            "pool": outcome.state.pool,
            "degraded": classification is None,
            # 剧情事件，不是判分反馈：前端在对话里渲染成一条旁白
            "pressure": outcome.pressured,
        },
    )

    if outcome.ending is not None:
        raw = await gateway.narrate_ending(
            ending=outcome.ending, trust=outcome.state.trust
        )
        yield Event(
            "ending",
            {
                "kind": outcome.ending.value,
                "trust": outcome.state.trust,
                "lines": _split_screened(raw),
            },
        )

    record = TurnRecord(
        round=round_,
        utterance=utterance,
        reply="".join(spoken),
        hits=tuple(hit_keys),
        grounded=grounded,
        delta=outcome.delta,
    )
    next_session = Session(
        gid=session.gid,
        state=outcome.state,
        history=session.history + (record,),
        # 开场白要一路带下去：服务端不存任何东西，令牌里没有的就是永远没有了，
        # 复盘要靠它才能还原出完整的对话
        opening=session.opening,
    )
    yield Event(
        "state", {"token": sign_session(next_session, secret=secret, issued_at=now)}
    )
    yield Event("done", {})
