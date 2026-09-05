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

from .classify import Classification, evidence_present, parse_classification
from .fallback import AVOID_WINDOW, ending_fallback, fallback_line
from .guard import breaker
from .scenario import scenario_for
from .safety import (
    SAFE_FALLBACK,
    absorb_injection,
    detect_real_world_risk,
    screen_sentence,
)
from .scoring import (
    MAX_ROUNDS,
    Ending,
    Mood,
    evaluate_turn,
    is_finished,
    mood_for,
    under_pressure,
)
from .state_token import Session, TurnRecord, sign_session
from .streaming import SentenceBuffer


class SessionFinished(Exception):
    """拿一张已经收过场的令牌继续推进。

    它是**状态机约束**，不是用户错误也不是服务端故障，所以既不降级也不兜底：
    这一局的最后一屏已经发出去了，再发一轮就会重复写一次终局和统计，
    而前端与服务端对"这一局有几轮"的认识会就此分叉。
    """


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
        result = parse_classification(await asyncio.wait_for(task, timeout))
        # 解析不出来也算一次故障：模型返回了东西，但格式对不上（P1-11 的严格
        # schema 会在格式漂移时返回 None）。那和超时一样，都是"这一轮判不了分"
        breaker.record(ok=result is not None)
        return result
    except asyncio.TimeoutError:
        logger.warning("分类超时（L2 降级），本轮按中性判分")
    except Exception:  # noqa: BLE001 - 网关抛什么都不该让这一轮作废
        logger.exception("分类失败（L2 降级），本轮按中性判分")
    breaker.record(ok=False)
    return None


@dataclass(frozen=True)
class Event:
    name: str
    data: Dict[str, Any] = field(default_factory=dict)


def _screened(
    sentences: Iterable[str],
    seen: Optional[set] = None,
    fallback: str = SAFE_FALLBACK,
) -> List[str]:
    """过安全层，并丢掉被剥成空壳的句子（整句只是一句舞台指示）。

    `seen` 用来在**同一轮内**去重兜底台词。安全层是整句替换，一轮里若有三句
    违规就会替出三句一模一样的话——实测真的发生过（模型吐了一段带链接的
    训练语料样板话，三句全中）：

        反正老师推的那只，我心里有数。
        反正老师推的那只，我心里有数。
        反正老师推的那只，我心里有数。

    连发三条同样的消息，比留个空档还像坏了。第一条留下，后面的丢掉。
    """
    out = []
    for sentence in sentences:
        text = screen_sentence(sentence, fallback)
        if text is None:
            continue
        if text == fallback and seen is not None:
            if fallback in seen:
                continue
            seen.add(fallback)
        out.append(text)
    return out


def _split_screened(text: str, fallback: str = SAFE_FALLBACK) -> List[str]:
    """把一整段文本切成句子再过安全层。

    结局台词是非流式拿到的一整段，但它下发时同样一句一个气泡——
    对玩家来说，最后那几句和前面每一轮没有任何区别。

    **结局这一屏尤其不能替出别人的台词**：它是玩家唯一会截图发出去的那一屏。
    """
    buffer = SentenceBuffer()
    return _screened(buffer.feed(text) + buffer.flush(), set(), fallback)


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
    # **终局封口。** 这一条要在任何副作用之前——分类请求、演绎请求、统计、
    # 语料，一样都不能因为一张过期的令牌再发生一次。
    #
    # HTTP 层也拦一道（`main._turn_events`）。两层不是冗余：引擎这一层保护的是
    # 直接调 `play_turn` 的调用方（跑批、蒙特卡洛、将来的其他入口），
    # HTTP 那一层保护的是"根本不该走到引擎"的请求。
    if is_finished(state):
        raise SessionFinished(
            f"这一局已经结束（round={state.round}, trust={state.trust}）"
        )

    round_ = state.round + 1
    # 场景由令牌里的 sid 取回（ADR-0003：服务端不存任何东西）。
    # 它决定演什么、兜底说什么，以及**此刻哪一招管用**（效力矩阵）。
    scene = scenario_for(session.sid)

    yield Event("meta", {"round": round_, "remaining": MAX_ROUNDS - round_})

    # 台词逐句下发，同时攒起来写进 history——复盘要靠它
    spoken: List[str] = []

    # 这一轮的台词是**谁写的**：模型，还是兜底台词库，还是注入吸收那条预置回应。
    # 随 `score` 事件下发（`line_source`），并在 /healthz 上累计。
    #
    # **加它的理由**：兜底台词是故意写得让玩家察觉不出来的（fallback.py 顶部：
    # "玩家未必察觉：骗子本来就说车轱辘话"），而运维那一侧同样看不出来——
    # 在此之前唯一的痕迹是 `logger.warning("演绎超时（L1 降级）")`，
    # 一行日志。于是"这台服务到底在用 claude-opus-5，还是每一轮都在发罐头"
    # 这个问题，只能靠翻日志回答。
    #
    # **`degraded` 不能兼任这件事**：那一位说的是*分类*没判成
    # （`classification is None`），与台词是谁写的是两件事——实测就撞见过
    # 一轮 `degraded: false` 而台词是罐头的（分类回来了，演绎超时了）。
    line_source = "model"

    # 真实人身安全信号优先于反操纵检查——两者同时命中时，先接住求救。
    # `or` 短路：`risk_reply` 非空就不会再看注入吸收，语义上正是这个优先级。
    risk_reply = detect_real_world_risk(utterance)
    absorbed = risk_reply or absorb_injection(utterance)
    if absorbed is not None:
        # 装听不懂，或者（优先级更高）接住一次真实求助：两种都不转发给模型。
        # 该轮记 neutral——不加不减，但仍吃信任流失；真实求助不该被扣信任度，
        # 可这条件同样不该单独放行——不然"假装在求助"会变成新的免罚话术，
        # 这一格的取舍与合规红线同理，留给后续观测数据去校正力度。
        buffer = SentenceBuffer()
        line_source = "safety_escalation" if risk_reply is not None else "absorbed"
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
        # 兜底台词整局只发一条，不是一轮只发一条（见 _screened 的文档字符串）。
        # **这里原来只在本轮内去重**：`seen_fallback` 每轮从空集合起步，于是安全层
        # 在第 3 轮替出一句「反正老师推的那只，我心里有数」、第 7 轮又撞上同一条
        # 规则，玩家会在同一局里看到两次一模一样的话——这正是"看着像兜底文案"
        # 最直接的证据，且与网关健不健康无关，纯粹是这个去重的作用域切错了。
        # 服务端不存会话（ADR-0003），但 `session.history` 本来就带着这一局
        # 全部已发生的台词随令牌走，不用为此新开一个字段——直接扫一遍就知道
        # 这一局用过没有。
        # 去重认的是**这个场景自己**那一句（`scene.safe_fallback`），
        # 不是安全层那个缺省值——否则换了场景，整局只发一条的约束就失效了
        seen_fallback: set = (
            {scene.safe_fallback}
            if any(scene.safe_fallback in record.reply for record in session.history)
            else set()
        )
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
                    # 第一句单独一档：心虚地客气，不是一上来就怼。
                    # 只换演法，判分那边一个参数都没动
                    first_turn=round_ == 1,
                    scene=scene,
                ):
                    for text in _screened(
                        buffer.feed(chunk), seen_fallback, scene.safe_fallback
                    ):
                        spoken.append(text)
                        yield Event("sentence", {"text": text})
                for text in _screened(
                    buffer.flush(), seen_fallback, scene.safe_fallback
                ):
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
                line_source = "fallback"
                # 同样避开最近那两句（窗口与离线模式共用 `AVOID_WINDOW`）。
                # 线上这条路是网关抖动才走到的，撞车概率远低于离线模式，
                # 但**连着降级两轮**恰恰是最容易撞的场景，而那正是玩家
                # 最可能截图的两轮
                text = fallback_line(
                    mood,
                    scene=scene,
                    avoid="".join(r.reply for r in session.history[-AVOID_WINDOW:]),
                )
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

    # 模型说它扎根在哪一句上（P1-12）。**不参与判分**——它进语料，
    # 给将来人工复核这批标签时当线索用。`evidence_present` 只写日志，
    # 理由见 classify.Classification.evidence 上面那段
    evidence = classification.evidence if classification else ""
    if evidence and grounded:
        pool = session.opening + "".join(r.reply for r in session.history)
        if evidence_present(evidence, pool) is False:
            logger.info(
                "分类给的引文在对话里找不到（不改分，只记一笔）: %r", evidence[:40]
            )

    outcome = evaluate_turn(
        state,
        hit_keys=hit_keys,
        grounded=grounded,
        # 场景唯一能动的两处判分参数。其余（钝化、扎根、防御姿态、追问窗口、
        # 阻力曲线、蓄势池、结局阶梯）全部共用——见 app/scenario.py 顶部
        efficacy_table=scene.efficacy,
        mistimed_moods=scene.mistimed_warning_moods,
    )
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
            # 只为落进语料，前端一个字都不显示——对局中显示"你引用了这一句"
            # 等于把扎根判据摊在玩家面前，那正是攻略化的开始
            "evidence": evidence,
            "pool": outcome.state.pool,
            # 信任流失与蓄势池释放。少了这两个，复盘上的账对不上：
            # 「第 3 轮 23 分，第 4 轮 +18」，结果却是 39——玩家会去算 23+18=41
            "drift": outcome.drift,
            "released": outcome.released,
            # 合规红线：本轮踩了几次、这一局累计几次。
            # **当轮就要下发**，不能只留到复盘——踩线那一刻的反馈才教得会人。
            # 累计数一并给，是因为复盘那张合规卡要在任何结局下都说得出总数，
            # 而前端自己数 hits 会与判分口径走散（哪些标签算红线只此一份）。
            "breached": outcome.breached,
            "breaches": outcome.state.breaches,
            # 那句「有据告知」是不是说早了。**必须单独下发**：判分那边已经把它
            # 换成了 bare_assertion（`scoring._retime`），前端只看 hits 的话，
            # 玩家会看到一个"空口断言"标签，然后完全不知道自己错在哪——
            # 他明明给了依据。错的不是那句话，是时候。
            "mistimed_warning": outcome.mistimed_warning,
            "degraded": classification is None,
            # 这一轮的台词是谁写的：model / fallback / absorbed。
            # **与 `degraded` 是两件事**（那一位说的是分类），理由见上面
            # `line_source` 的声明处。前端不渲染它——兜底台词本来就是
            # 写给玩家察觉不出来的，这一位是给运维和评委看的。
            "line_source": line_source,
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
                    scene=scene,
                ),
                scene.safe_fallback,
            )
        except Exception:  # noqa: BLE001 - 判分已经下发了，这一屏绝不能再丢
            logger.exception("结局台词生成失败，改用预置收尾")
            lines = []
        # 生成失败或被安全层剥空时用预置收尾。逐轮台词降级还能靠
        # "骗子本来就说车轱辘话"糊过去，最后一屏糊不过去：玩家会看到
        # 判分跳完之后对话直接断掉，连一句收尾都没有。
        #
        # **收口成一个变量**（2026-09-03）：下面线索覆盖要数的是他真正说出口的
        # 那批话，而复盘那一侧数的是下发出去的 `lines`——原先这里现算现发，
        # 降级到预置收尾时两侧数的就不是同一批文本了。
        final_lines = lines or list(ending_fallback(outcome.ending, scene))
        yield Event(
            "ending",
            {
                "kind": outcome.ending.value,
                "trust": outcome.state.trust,
                "lines": final_lines,
                # **本场景的效力矩阵，只在结局这一屏下发。**
                #
                # 这是全作品唯一无法被竞品复制的那条判据（「判的是用得是不是
                # 时候，不是说得标不标准」），而在此之前它**只活在文档里**：
                # 玩家打完一局看到的是一个平均倍率数字，不是"同一句话换个
                # 时候值多少"的对照。复盘里那个对照块要用它。
                #
                # 对局中一个字都不发——POSITIONING 那条「在对局中显示分数
                # ＝把攻略印在屏幕上」管的是对局中。打完了给他看，那不叫泄题，
                # 那就是复盘本身要干的事（钥匙条早就在显示效力倍率了）。
                #
                # 也因此**前端仍然不许抄一份**（踩过的坑 7）：调参数时蒙特卡洛
                # 会重跑，这一份跟着下发走，两边不会走散。
                "efficacy": {
                    key: {mood.value: row[mood] for mood in Mood}
                    for key, row in scene.efficacy.items()
                },
                # **线索覆盖：这一局他抖出来了几条。**
                #
                # 它**不给前端显示用**——复盘那一侧自己按同一批正则算一遍
                # （`paintPhone`），两处都算是有意的：那边算是为了显示，
                # 这里算是为了落数，而客户端报上来的数字不能当指标用。
                #
                # 定级是「机制自证指标」，不进成功标准（POSITIONING「成功标准」
                # 那一节的主指标仍然只有放弃率与撤单率，参与度指标一律作废）。
                # 它回答的是另一个问题：**这一局的信息差到底成立没有。**
                # 一个打满全场、一条线索都没露出来的对局，无论结局落在哪一档
                # 都是空转，而在此之前没有任何一个字段看得见这件事。
                "clues": {
                    "got": scene.clues_surfaced(
                        [session.opening]
                        + [record.reply for record in history]
                        + final_lines
                    ),
                    "of": len(scene.diggable),
                },
                # **隐藏线索在这里下发，不在开局**（P1-14）。
                # 他手机上那几条是这一局要挖的答案；开局响应里带着它，
                # 打开开发者工具就能提前看完。挪到这一刻，对正常玩家
                # 没有任何差别——他本来也是打完才看见的。
                **scene.reveal(),
            },
        )

    next_session = Session(
        gid=session.gid,
        sid=session.sid,
        state=outcome.state,
        history=history,
        # 开场白要一路带下去：服务端不存任何东西，令牌里没有的就是永远没有了，
        # 复盘要靠它才能还原出完整的对话
        opening=session.opening,
        # 开局上下文同理。掉了它，第 2 轮起这一局就不再属于任何一条异动、
        # 任何一个实验组——而那正是转向之后要算的第一个数
        origin=session.origin,
    )
    yield Event(
        "state", {"token": sign_session(next_session, secret=secret, issued_at=now)}
    )
    yield Event("done", {})
