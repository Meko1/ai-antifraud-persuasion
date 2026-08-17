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
from .fallback import ending_fallback, fallback_line
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

# L1 降级线：演绎超时就改用兜底台词。
ACT_TIMEOUT_SECONDS = 6.0

# 分类的预算。比演绎宽——分类不流式，它整个耗时都被台词的流式输出掩着——
# 但**必须有一个**。这里原先是裸 await，实际走的是 LLM_TIMEOUT_SECONDS
# （默认 30 秒）：台词播完之后玩家还可能干等二十几秒才看到判分跳出来。
CLASSIFY_TIMEOUT_SECONDS = 10.0


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


async def _classified(task: "asyncio.Task[str]", *, timeout: float) -> Optional[Classification]:
    """等分类结果。超时或出错一律退回 None（＝按中性判分，degraded 置位）。

    分类**不重试也不猜**：拿不到就按"既没命中钥匙也没触发失误"记，玩家仍吃
    这一轮的信任流失。这比让整轮作废好得多——作废意味着已经播出去的台词
    留在聊天记录里，令牌却停在上一轮，服务端当这一轮压根没发生过。
    """
    try:
        return parse_classification(await asyncio.wait_for(task, timeout))
    except asyncio.TimeoutError:
        logger.warning("分类超时（L2 降级），本轮按中性判分")
    except Exception:  # noqa: BLE001 - 网关抛什么都不该让这一轮作废
        logger.exception("分类失败（L2 降级），本轮按中性判分")
    return None


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
    classify_timeout: float = CLASSIFY_TIMEOUT_SECONDS,
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
            try:
                async for chunk in _act_with_deadline(
                    gateway,
                    mood=mood,
                    utterance=utterance,
                    timeout=act_timeout,
                    history=session.history,
                    opening=session.opening,
                    # 每局的人格变体由 gid 派生（app/persona.py）。服务端不存任何
                    # 东西，gid 本来就在签名令牌里，hash 一下就够（ADR-0003）
                    gid=session.gid,
                    # 该轮王老师又催了一遍：判分要多扣 3 分，台词也得跟着紧张起来。
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
            except Exception:  # noqa: BLE001 - 网关抛什么都不该让这一轮作废
                # 抛错和超时是同一类事故，处理方式必须一样。少了这一条，
                # LLMError 会一路冒到 main：那一轮既没有判分也没有新令牌，
                # 而台词已经播出去了——聊天记录里多一段对话，服务端当它没发生。
                logger.exception("演绎失败（L1 降级），改用兜底台词")

            if not spoken:
                # L1：演绎降级。绝不给玩家一片空白。
                text = fallback_line(mood)
                spoken.append(text)
                yield Event("sentence", {"text": text})

            classification = await _classified(
                classification_task, timeout=classify_timeout
            )
        finally:
            # 玩家中途关掉页面时这个生成器会被 aclose()，上面的 await 一个都不执行。
            # 不收这一手，分类请求就成了没人认领的孤儿：日志里刷
            # "Task exception was never retrieved"，额度也白花。
            if not classification_task.done():
                classification_task.cancel()

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
            # 复盘要解释"这一招为什么只值这么点"，用的是**判分时**的那一档
            # 与效力矩阵里对应的那一格。两个数都由服务端算好，
            # 前端不抄那张表——抄了，调参时蒙特卡洛会重跑，页面却不会自己动
            "judged_mood": outcome.judged_mood.value,
            "efficacy": outcome.efficacy,
            # 追问窗口：复盘要凭它决定说不说那句"没乘胜追击"
            "window_result": outcome.window_result,
            "window_opened": outcome.window_opened,
            "hits": list(hit_keys),
            "grounded": grounded,
            "pool": outcome.state.pool,
            "degraded": classification is None,
            # 剧情事件，不是判分反馈：前端在对话里渲染成一条旁白
            "pressure": outcome.pressured,
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
    history = session.history + (record,)

    if outcome.ending is not None:
        # **历史必须传进去**，而且要含刚刚这一轮。少了它，整局的最后一屏是在
        # 零上下文下生成的：他只能说一段谁都适用的场面话，接不住玩家刚说的
        # 任何一句——而这一屏恰恰是玩家唯一会截图发出去的那一屏。
        try:
            lines = _split_screened(
                await gateway.narrate_ending(
                    ending=outcome.ending,
                    gid=session.gid,
                    history=history,
                    opening=session.opening,
                )
            )
        except Exception:  # noqa: BLE001 - 判分已经下发了，这一屏绝不能再丢
            logger.exception("结局台词生成失败，改用预置收尾")
            lines = []
        yield Event(
            "ending",
            {
                "kind": outcome.ending.value,
                "trust": outcome.state.trust,
                # 生成失败或被安全层剥空时用预置收尾。逐轮台词降级还能靠
                # "骗子本来就说车轱辘话"糊过去，最后一屏糊不过去：玩家会看到
                # 判分跳完之后对话直接断掉，连一句收尾都没有。
                "lines": lines or list(ending_fallback(outcome.ending)),
            },
        )

    next_session = Session(
        gid=session.gid,
        state=outcome.state,
        history=history,
        # 开场白要一路带下去：服务端不存任何东西，令牌里没有的就是永远没有了，
        # 复盘要靠它才能还原出完整的对话
        opening=session.opening,
    )
    yield Event(
        "state", {"token": sign_session(next_session, secret=secret, issued_at=now)}
    )
    yield Event("done", {})
