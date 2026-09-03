"""演绎跑批：量「AI 味」与「不重样」。

判分那一侧有两把尺子（`balance_sim` 守规则表，`classify_eval` 守标签），
演绎这一侧一把都没有——老陈说得像不像人、两个玩家看到的是不是同一个老陈，
一直靠打几局凭感觉。凭感觉量得出"像不像"，量不出"重不重样"：
同一句话在两局之间重复，单看一局的人永远发现不了。

本脚本**会真实调用模型**，因此不进 pytest（与 `classify_eval` 同一条理由）。
指标口径本身由 `tests/test_act_eval.py` 守着，那部分不调模型。

用法：
    python -m tools.act_eval                       # 全量：4 路线 × 20 遍（默认场景）
    python -m tools.act_eval --scenario zhou       # 换一个场景跑
    python -m tools.act_eval --runs 1 --rounds 2   # 几毛钱确认链路通
    python -m tools.act_eval --routes climb        # 只跑一条路线
    python -m tools.act_eval --dump lines.jsonl    # 导出台词，人格那一类靠人眼看
    python -m tools.act_eval --drift lines.jsonl   # 拿导出的台词跑分类器，看扎根率有没有漂

**玩家的话是写死的**（见 tests/data/act_routes.jsonl 顶部注释）：要量的是同样
输入下老陈有多不一样，输入一变这个指标当场作废。情绪档位同样是声明的，
不由分类器现判——否则 20 遍之间的差异里会混进"档位走到了别处"这个变量。

## 一次只跑一个场景（2026-08-22）

**在此之前这个脚本结构上就跑不了别的场景**：`opening_for(gid)` 不传 personas、
`gateway.act(...)` 不传 `scene=`，两处都落到缺省的老陈。于是历史上每一次
"演绎基线"量的都只是老陈一个人——8-21 那组三条门槛全过的数字，对
zhou / liu / ben 一个字都不成立。

路线也必须按场景各写一份：拿荐股局的话（王老师、那只票）去问周淑琴，
量出来的是"她在答非所问"，不是她演得好不好。

**指标一次只在一个场景内部汇总。** 跨场景合并没有意义——`cross_route_repeat`
认的是"同一句话出现在两条路线里"，四个场景的人根本不说同一种话，
合起来算只会把这个指标稀释成 0。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import sys
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from tools.console import FAIL, guard
from app.llm import llm_client
from app.persona import opening_for
from app.safety import screen_sentence
from app.scenario import DEFAULT, SCENARIOS, Scenario, scenario_for
from app.scoring import Mood, under_pressure
from app.state_token import TurnRecord
from app.streaming import SentenceBuffer

DEFAULT_ROUTES_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "data" / "act_routes.jsonl"
)

# ── 书面语黑名单 ───────────────────────────────────────────────────────────
#
# 老陈五十二岁，机械厂干了二十年，在微信上打字。下面每一个词他都不会用——
# 出现即是模型的默认文风顶穿了人设。分三类，各自的病因不同：
#
# 1. 书面连接词：模型写说明文的肌肉记忆，聊天窗口里一个都不该出现
# 2. 客服腔：被 RLHF 训出来的礼貌，是"AI 味"里最容易被一眼认出的那部分
# 3. 结构性口头禅："第一…第二…"这类，人吵架时不会给你列条目
#
# 这份表只增不改：删掉一个词等于把过去所有跑批的数字变得不可比。
FORMAL_WORDS: Tuple[str, ...] = (
    # 书面连接词
    "然而", "因此", "此外", "首先", "其次", "再者", "综上", "总而言之",
    "并且", "以及", "从而", "例如", "譬如", "倘若", "鉴于", "由此可见",
    "一方面", "另一方面", "换言之", "与此同时", "值得注意",
    # 客服腔
    "您", "请您", "建议您", "我理解", "我明白你的", "感谢你的", "抱歉",
    "不妨", "可以考虑", "如有", "请放心", "祝你",
    # 结构性口头禅
    "第一点", "第二点", "总结一下", "简单来说", "客观来说",
)

# 破折号与省略号本身不是错——老陈会说"我这……我再想想"。
# 但成对破折号「——」是书面语的标志，聊天框里没人这么打。
FORMAL_MARKS = ("——",)

# 舞台指示。安全层会把它剥掉（app/safety.strip_stage_directions），
# 所以玩家看不到；但它剥掉的次数正是模型出戏的频率，必须单独记。
_STAGE = re.compile(r"[（(][^（()）]{0,20}[)）]")

# 一整段里连着出现的英文。判据与安全层 `_not_his_words` 同源（拉丁字母够多且
# 占比够高），但**量的对象不同**：安全层按句判、判完就丢，这里按轮判整段 raw，
# 为的是把"模型确实吐了英文"这件事留在指标里，而不是被安全层一擦了之。
#
# 阈值比安全层松：安全层要在一句话上做取舍（宁可丢），这里只做统计，
# 只要整段里出现连着 12 个以上拉丁字母的成分就算中招。
_ENGLISH_RUN = re.compile(r"[A-Za-z][A-Za-z'\s,]{11,}")


# 中文旁白的判据。**比安全层的词表宽**，这是故意的：安全层要为每一次拦截
# 负责（拦错了就是吞掉老陈的真话），指标只负责数，宁可多数几个也别漏报。
# 所以这里还收了第三人称叙述那一类——安全层不敢拦，指标必须看得见。
_META = re.compile(
    r"用户|助手|人物设定|人设|符合设定|提示词|系统提示|旁白|台词"
    r"|老陈(会|这时候|的反应)|李经理(这句|刚刚|刚才)"
)


def _has_english_leak(raw: str) -> bool:
    """整段里有没有成句的英文。A 股、K 线这类单词不算。"""
    return any(
        sum(1 for ch in m.group(0) if ch.isalpha()) >= 12
        for m in _ENGLISH_RUN.finditer(raw)
    )

# 跑批容错。生产上客户端是 max_retries=0 的（演绎超时要走 L1 降级，
# SDK 在那儿默默重试会把 6 秒预算翻倍），但跑批不一样：一次瞬时 5xx
# 不该毁掉十几分钟、上千次调用的一整轮跑批。实测网关会偶发
# `upstream connect error or disconnect/reset before headers`。
#
# 失败的轮次**从指标里剔除，不当成空台词**——空字符串会被当作"和另一条
# 空的一模一样"，凭空抬高首句重复率，那比丢掉这一轮更糟。
ACT_RETRIES = 2
RETRY_BACKOFF = 2.0

# **限流要另算一档退避**（2026-09-03）。
#
# 上面那 2 秒是照"瞬时 5xx"定的，而网关真正会拒的是限流：
# `RateLimitError: 429 … 该令牌对模型的 RPM 已经到达上限`。RPM 是一个
# **按分钟算的窗口**，2 秒和 4 秒两次重试全落在同一个窗口里，退了等于没退。
#
# 实测（48 次调用一组）：并发 8 挂 54%、并发 4 挂 23%、并发 3 挂 8%，
# 而三档的**成功吞吐都是每分钟 30 次左右**——那就是上限本身。
# 也就是说抬并发一次吞吐都买不到，只买到失败率。
#
# 失败在这个脚本里不是"少几个样本"那么轻：`tools/cue_coverage.py` 数的是
# 一局里线索抖出来没有，**缺掉的轮次会把覆盖率系统性压低**，
# 而那正是要量的东西。所以宁可退得久一点。
#
# 加抖动是因为并发的几路会在同一刻一起撞上限、再一起退避同样的时长，
# 醒来又是同一刻——不抖的话它们会一直同步，等于并发数从没降下来过。
RATE_LIMIT_BACKOFF = 20.0
RATE_LIMIT_MARKERS = ("RateLimitError", "429", "RPM")


def _backoff_for(exc: BaseException, attempt: int) -> float:
    """这一次该退多久。限流走长退避 + 抖动，其余照旧。"""
    text = str(exc)
    base = (RATE_LIMIT_BACKOFF
            if any(m in text for m in RATE_LIMIT_MARKERS)
            else RETRY_BACKOFF)
    return base * (attempt + 1) * (1.0 + random.random() * 0.5)


@dataclass(frozen=True)
class Route:
    # 路线 id 是**施压形状**（cold / climb / sawtooth / parrot），四个场景各有
    # 同名的一条。它不带场景前缀是刻意的：跑批一次只跑一个场景，报告里的
    # 「路线」那一列因此不必重复四遍场景名，而形状之间才是可比的
    # ——chen 的 cold 与 zhou 的 cold 量的是同一种玩家。
    id: str
    sid: str
    note: str
    moods: Tuple[Mood, ...]
    utterances: Tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.moods) != len(self.utterances):
            raise ValueError(f"路线 {self.sid}/{self.id}：档位与发言数量不一致")


@dataclass(frozen=True)
class Reply:
    """一轮里老陈说的话。"""

    route: str
    run: int
    round: int
    utterance: str               # 玩家那一句，认"复述反问"要用
    sentences: Tuple[str, ...]   # 过完安全层的，玩家真正看到的
    raw: str                     # 模型原样吐出来的，用来量出戏频率

    @property
    def text(self) -> str:
        return "".join(self.sentences)

    @property
    def first(self) -> str:
        return self.sentences[0] if self.sentences else ""


@dataclass(frozen=True)
class Report:
    replies: int
    runs_per_route: int
    # 同一路线同一轮，几遍之间一模一样的比例。
    # 首轮基线实测 0.0%——整段原样重复几乎不会发生，这个数字单看是漂亮的假象
    duplicate_rate: float
    # **真正的主指标**：只比第一条消息。基线 0.0% vs 11.6%（cold 路线 30.4%）——
    # 整段不重样，开口那一下却高度可预测。玩家在聊天窗口里最先看到的就是它，
    # 两个人互相截图，对上的也是它。
    first_dup_rate: float
    # 把对方的词原样弹回来当反问（"你怎么这么固执" → "我固执？"）。
    # 这是模型的条件反射，不是老陈的性格：真人偶尔这么怼，不会二十遍里怼十一遍
    echo_question_rate: float
    # 同上，但按四元字组的 Jaccard 均值算——措辞换了但骨架没换会在这里现形
    ngram_overlap: float
    # 整句在不同路线之间原样复现的比例：模型的固定套话
    cross_route_repeat: float
    formal_rate: float           # 命中书面语黑名单的句子占比
    stage_direction_rate: float  # 模型写了括号动作的轮次占比
    # **思考过程用英文漏进台词的轮次占比。** 量的是 raw，不是过完安全层的句子——
    # 安全层已经把它们丢掉了，只看下发内容永远是 0.0%，等于把这个病测没了。
    #
    # 它是关掉 thinking 的已知代价（§6.4），而 thinking 必须关。上一次基线里
    # 959 轮中 15 轮、80 局中 6 局中招（十三局就有一局），玩家会看到
    # 「I think the intended structure is that I write the assistant response…」。
    # 提示词与安全层都改过了，但**这条跑批一次都没验过** —— 加这个指标就是为了验它。
    english_leak_rate: float
    # **中文旁白漏到玩家眼前的轮次占比。** 与上面那条的分工要看清楚：
    # 英文那条量 raw（安全层全都拦住了，量下发内容永远是 0），这条**量下发内容**，
    # 因为中文旁白安全层拦不干净——量的就是"玩家真会看到多少"。
    #
    # 2026-08-17 首次测得 2.0%（954 轮里 19 轮）。最坏的一轮里模型用
    # 「用户」「助手」当角色标签，把整段对话两边都写了，一轮漏八句。
    # 词表兜底 + 提示词改口之后要看这个数掉到哪儿。
    meta_leak_rate: float
    sentences_per_turn: Tuple[float, float]  # 均值 / 标准差
    sentence_length: Tuple[float, float]
    top_formal: Tuple[Tuple[str, int], ...]
    top_repeats: Tuple[Tuple[str, int], ...]
    # 每条路线的（首句重复率，整段重复率，字组重合）。
    # 必须按路线拆：总体 11.6% 看着还行，摊开才发现全压在 cold 那一条上，
    # 而 cold 正是路人的打法——最可能来投票的那批人，看到的重样最多
    by_route: Dict[str, Tuple[float, float, float]] = field(default_factory=dict)


# ── 验收门槛 ───────────────────────────────────────────────────────────────
#
# 只有两条进门槛，其余指标只打印。理由与 §9.4 一致：门槛要能一句话说清
# "违反了会死"。句长方差、每轮条数没有这样的死线，设了就是拍脑袋，
# 而一个拍出来的门槛会在半年后拦住一次正当的改动。
#
# 这三条能：
# · 首句重复——投票日两个同事互相截图，对上的就是开口那一句，"活的受害者"
#   这个唯一卖点当场破产（docs/REDESIGN-TRAINER.md 调研发现一）
# · 复述反问——把对方的词原样弹回来，是模型的条件反射，不是老陈的性格
# · 书面语——它是"这是个 AI"最短的证据，一个词就够
#
# 数字取自实测，不是拍的。守的是"别退回去"，不是"再进一步"：
# 门槛的作用是拦住悄悄的劣化，不是逼着后来的人一直往上抬。
#
# 两次实测（2026-08-15，各 4 路线 × 20 遍 × 12 轮 = 960 次演绎调用）：
#
#                    改造前    人格变体上线后
#     首句重复        11.6%        5.6%
#       └ cold        30.4%       12.5%
#     复述反问        11.2%        7.5%
#     字组重合         3.1%        1.5%
#     跨路线套话       4.5%        2.1%
#     书面语           0.0%        0.0%
#
# 门槛取在实测与基线之间、偏实测那一侧：守的是"别退回改造前"，
# 不是"必须一直更好"。留的余量给两件事——20 遍的抽样噪声，
# 以及换模型。它不该逼着后来的人为了过线去调一个本来没问题的东西。
FIRST_DUP_CEILING: Optional[float] = 0.08
ECHO_QUESTION_CEILING: Optional[float] = 0.10
# 书面语基线是 0.0%（960 轮里只有一个"您"）。守 2% 不花任何余量，
# 与 classify_eval 的 PARROT_GROUNDED_FLOOR 是同一个道理：白给的安全线要拿着
FORMAL_CEILING: Optional[float] = 0.02


def load_routes(
    path: Path = DEFAULT_ROUTES_PATH, sid: Optional[str] = None
) -> Tuple[Route, ...]:
    """读回放路线。`sid` 给了就只留那个场景的。

    不给 sid 时返回全部——那是给测试用的（要遍历四个场景各查一遍），
    跑批一律经 `--scenario` 过滤，理由见模块文档。
    """
    routes: List[Route] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        data = json.loads(line)
        if sid is not None and data["sid"] != sid:
            continue
        routes.append(
            Route(
                id=data["id"],
                sid=data["sid"],
                note=data.get("note", ""),
                moods=tuple(Mood(m) for m in data["moods"]),
                utterances=tuple(data["utterances"]),
            )
        )
    return tuple(routes)


# ── 跑批 ───────────────────────────────────────────────────────────────────


async def play_route(
    route: Route,
    gateway: object,
    *,
    run: int,
    scene: Optional[Scenario] = None,
    rounds: int = 0,
    failures: Optional[List[Tuple[str, int, int]]] = None,
) -> List[Reply]:
    """跑一遍路线。走生产同一条路：同一个网关、同一份提示词、同一个安全层。

    gid 由 (场景, route, run) 拼出来，于是人格变体与开场白都跟生产一样由它派生。
    20 遍拿到 20 个不同的 gid——这正是玩家那一侧的真实情况：
    每个人开一局就是一个新 gid，落到哪个变体身上由 hash 决定。
    **gid 里必须带场景**：不带的话四个场景会挑到同一串变体下标，
    "换个场景重跑一遍"就变成了"同一批人换套剧本"，抽样根本没换。

    `scene` 决定三件事，缺一件这一批就是在量老陈：开场白从哪一组人格里挑、
    提示词用谁的剧本、四档演绎指示是谁的。**这三处 8-22 之前一处都没传。**
    """
    scene = scene or DEFAULT
    gid = f"{scene.id}:{route.id}:{run}"
    opening = opening_for(gid, scene.personas)
    failures = [] if failures is None else failures
    history: List[TurnRecord] = []
    replies: List[Reply] = []
    turns = rounds or len(route.utterances)

    for index in range(turns):
        round_ = index + 1
        raw = None
        for attempt in range(ACT_RETRIES + 1):
            try:
                buf = ""
                async for chunk in gateway.act(  # type: ignore[attr-defined]
                    mood=route.moods[index],
                    utterance=route.utterances[index],
                    history=tuple(history),
                    opening=opening,
                    pressured=under_pressure(round_),
                    # 生产上第 1 轮走的是 FIRST_TURN_DIRECTION，不是档位指示
                    # （app/engine.py 的 `first_turn=round_ == 1`）。跑批一直没传，
                    # 于是**主指标"首句重复率"量的是一句生产里根本不会出现的话**。
                    # 开口那一下正是这个脚本存在的理由，不能量错。
                    first_turn=round_ == 1,
                    gid=gid,
                    scene=scene,
                ):
                    buf += chunk
                raw = buf
                break
            except Exception as exc:  # noqa: BLE001 - 瞬时故障不该中断跑批
                if attempt == ACT_RETRIES:
                    # **消息也要印**（2026-09-03）。原先只印异常类型名，
                    # 于是一批 960 次调用全灭时，日志上是几百行一模一样的
                    # `LLMError`，既看不出是限流、是拒答还是网关挂了，
                    # 也就没法决定该降并发、该换 provider 还是该改提示词。
                    # 截断到 200 字：错误体可能很长，而跑批日志要还能读。
                    detail = " ".join(str(exc).split())[:200]
                    print(
                        f"  ! {route.id}#{run} 第 {round_} 轮放弃："
                        f"{type(exc).__name__}: {detail}",
                        flush=True,
                    )
                else:
                    await asyncio.sleep(_backoff_for(exc, attempt))
        if raw is None:
            failures.append((route.id, run, round_))
            # 这一轮没有台词，后面几轮的历史因此缺一块。照跑不误：
            # 缺一轮的历史仍然是合法输入，而中断整条路线会丢掉更多样本
            continue

        buffer = SentenceBuffer()
        # 兜底句按场景取。**跑批这一处传错，量出来的就是假的**：8-23 的 hang
        # 基线里「反正老师推的那只，我心里有数」出现 14 次，正是这句荐股局的
        # 台词被替进了顾之然那一局——它同时污染了"被重复最多的整句"这一栏
        screened = [
            text
            for sentence in buffer.feed(raw) + buffer.flush()
            if (text := screen_sentence(sentence, scene.safe_fallback)) is not None
        ]
        replies.append(
            Reply(
                route=route.id,
                run=run,
                round=round_,
                utterance=route.utterances[index],
                sentences=tuple(screened),
                raw=raw,
            )
        )
        history.append(
            TurnRecord(
                round=round_,
                utterance=route.utterances[index],
                reply="".join(screened),
                hits=(),
                grounded=False,
                delta=0,
            )
        )
    return replies


async def run(
    routes: Sequence[Route],
    gateway: object,
    *,
    runs: int,
    scene: Optional[Scenario] = None,
    rounds: int = 0,
    concurrency: int = 4,
) -> List[Reply]:
    gate = asyncio.Semaphore(concurrency)

    failures: List[Tuple[str, int, int]] = []

    async def one(route: Route, run_index: int) -> List[Reply]:
        async with gate:
            return await play_route(
                route,
                gateway,
                run=run_index,
                scene=scene,
                rounds=rounds,
                failures=failures,
            )

    batches = await asyncio.gather(
        *(one(r, i) for r in routes for i in range(runs))
    )
    if failures:
        print(f"\n{len(failures)} 轮重试三次仍失败，已从指标中剔除\n", flush=True)
    return [reply for batch in batches for reply in batch]


# ── 指标 ───────────────────────────────────────────────────────────────────


def _ngrams(text: str, n: int = 4) -> set:
    return {text[i : i + n] for i in range(max(0, len(text) - n + 1))}


def _jaccard(a: str, b: str) -> float:
    x, y = _ngrams(a), _ngrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def _longest_common(a: str, b: str) -> int:
    """最长公共子串长度。用来认出"复述"——不能只查整句包含：
    玩家说"你怎么这么固执"，老陈回"我固执？"，弹回来的是里面那两个字。"""
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


def is_echo_question(first: str, utterance: str) -> bool:
    """首句是不是"把对方的词原样弹回来"式的短反问。

    三个条件缺一不可：短、以问号收尾、与玩家那句有两个字以上的公共子串。
    只要长一点或者带上他自己的内容，就不再是条件反射，而是真的在顶嘴。
    """
    text = first.strip()
    if len(text) > 8 or not text.endswith(("？", "?")):
        return False
    return _longest_common(text.rstrip("？?"), utterance) >= 2


def _mean_std(values: Sequence[float]) -> Tuple[float, float]:
    if not values:
        return 0.0, 0.0
    return (
        statistics.fmean(values),
        statistics.pstdev(values) if len(values) > 1 else 0.0,
    )


def summarize(replies: Sequence[Reply], runs_per_route: int) -> Report:
    """把逐轮台词压成指标。

    重复率与 n-gram 重合都**只在同一路线同一轮内部**比较：跨轮比是没有意义的，
    第 3 轮和第 9 轮本来就该说不同的话。
    """
    groups: Dict[Tuple[str, int], List[Reply]] = {}
    for reply in replies:
        groups.setdefault((reply.route, reply.round), []).append(reply)

    dup_rates: List[float] = []
    first_dups: List[float] = []
    overlaps: List[float] = []
    by_route: Dict[str, List[List[float]]] = {}
    for (route, _), batch in groups.items():
        if len(batch) < 2:
            continue
        texts = [r.text for r in batch]
        firsts = [r.first for r in batch]
        dup = 1 - len(set(texts)) / len(texts)
        first_dup = 1 - len(set(firsts)) / len(firsts)
        pairs = [_jaccard(a, b) for a, b in combinations(texts, 2)]
        overlap = statistics.fmean(pairs) if pairs else 0.0
        dup_rates.append(dup)
        first_dups.append(first_dup)
        overlaps.append(overlap)
        slot = by_route.setdefault(route, [[], [], []])
        slot[0].append(first_dup)
        slot[1].append(dup)
        slot[2].append(overlap)

    echoes = sum(1 for r in replies if is_echo_question(r.first, r.utterance))

    # 跨路线复现：同一句话出现在两条不同路线里，说明它是模型的固定套话，
    # 与玩家说了什么无关——这类句子是"AI 味"里最致命的一种
    by_sentence: Dict[str, set] = {}
    for reply in replies:
        for sentence in reply.sentences:
            by_sentence.setdefault(sentence, set()).add(reply.route)
    all_sentences = [s for reply in replies for s in reply.sentences]
    cross = sum(1 for s in all_sentences if len(by_sentence[s]) > 1)

    formal_hits: Dict[str, int] = {}
    formal_sentences = 0
    for sentence in all_sentences:
        hit = False
        for word in (*FORMAL_WORDS, *FORMAL_MARKS):
            if word in sentence:
                formal_hits[word] = formal_hits.get(word, 0) + 1
                hit = True
        formal_sentences += hit

    staged = sum(1 for reply in replies if _STAGE.search(reply.raw))
    leaked = sum(1 for reply in replies if _has_english_leak(reply.raw))
    meta = sum(
        1 for reply in replies if any(_META.search(s) for s in reply.sentences)
    )
    repeats = sorted(
        ((s, sum(1 for x in all_sentences if x == s)) for s in by_sentence),
        key=lambda kv: -kv[1],
    )

    return Report(
        replies=len(replies),
        runs_per_route=runs_per_route,
        duplicate_rate=statistics.fmean(dup_rates) if dup_rates else 0.0,
        first_dup_rate=statistics.fmean(first_dups) if first_dups else 0.0,
        echo_question_rate=echoes / len(replies) if replies else 0.0,
        ngram_overlap=statistics.fmean(overlaps) if overlaps else 0.0,
        cross_route_repeat=cross / len(all_sentences) if all_sentences else 0.0,
        formal_rate=formal_sentences / len(all_sentences) if all_sentences else 0.0,
        stage_direction_rate=staged / len(replies) if replies else 0.0,
        english_leak_rate=leaked / len(replies) if replies else 0.0,
        meta_leak_rate=meta / len(replies) if replies else 0.0,
        sentences_per_turn=_mean_std([len(r.sentences) for r in replies]),
        sentence_length=_mean_std([len(s) for s in all_sentences]),
        top_formal=tuple(sorted(formal_hits.items(), key=lambda kv: -kv[1])[:8]),
        top_repeats=tuple(kv for kv in repeats[:8] if kv[1] > 1),
        by_route={
            route: (statistics.fmean(f), statistics.fmean(d), statistics.fmean(o))
            for route, (f, d, o) in by_route.items()
        },
    )


def check_thresholds(report: Report) -> List[str]:
    """返回未通过的门槛说明；空列表表示全部通过。

    门槛数字未标定时一律放行——一个空想出来的门槛比没有门槛更坏。
    """
    failures = []
    if FIRST_DUP_CEILING is not None and report.first_dup_rate > FIRST_DUP_CEILING:
        failures.append(
            f"首句重复率 {report.first_dup_rate:.1%} > {FIRST_DUP_CEILING:.0%}"
            "——两个同事互相截图，对上的就是开口那一句"
        )
    if (
        ECHO_QUESTION_CEILING is not None
        and report.echo_question_rate > ECHO_QUESTION_CEILING
    ):
        failures.append(
            f"复述反问率 {report.echo_question_rate:.1%} > {ECHO_QUESTION_CEILING:.0%}"
            "——把对方的词原样弹回来，是模型的条件反射，不是老陈的性格"
        )
    if FORMAL_CEILING is not None and report.formal_rate > FORMAL_CEILING:
        failures.append(
            f"书面语命中率 {report.formal_rate:.1%} > {FORMAL_CEILING:.0%}"
            "——一个词就够暴露对面是模型"
        )
    return failures


# ── 报告 ───────────────────────────────────────────────────────────────────


def format_report(report: Report) -> str:
    sent_mean, sent_std = report.sentences_per_turn
    len_mean, len_std = report.sentence_length
    lines = [
        f"共 {report.replies} 轮台词，每条路线跑 {report.runs_per_route} 遍",
        "",
        "不重样",
        f"  首句重复率（开口那一下，主指标）    {report.first_dup_rate:>7.1%}",
        f"  整段重复率（同路线同轮，一模一样）  {report.duplicate_rate:>7.1%}",
        f"  四元字组重合（措辞换了骨架没换）    {report.ngram_overlap:>7.1%}",
        f"  跨路线复现（与玩家说什么无关的套话）{report.cross_route_repeat:>7.1%}",
        "",
        "AI 味",
        f"  复述反问率（把对方的词弹回来）      {report.echo_question_rate:>7.1%}",
        f"  书面语命中率（按句）                {report.formal_rate:>7.1%}",
        f"  括号动作出戏率（按轮，安全层剥掉了）{report.stage_direction_rate:>7.1%}",
        f"  英文思考外漏（按轮，安全层丢掉了）  {report.english_leak_rate:>7.1%}",
        f"  中文旁白外漏（按轮，玩家真会看到） {report.meta_leak_rate:>8.1%}",
        "",
        "说话形状",
        f"  每轮几条消息    {sent_mean:>5.2f} ± {sent_std:.2f}",
        f"  每条多少个字    {len_mean:>5.1f} ± {len_std:.1f}",
    ]
    if report.by_route:
        lines += ["", f"{'路线':<12}{'首句重复':>10}{'整段重复':>10}{'字组重合':>10}"]
        for route, (first, dup, overlap) in sorted(report.by_route.items()):
            lines.append(f"{route:<12}{first:>10.1%}{dup:>10.1%}{overlap:>10.1%}")
    if report.top_formal:
        lines += ["", "书面语命中最多的词"]
        lines += [f"  {word} × {n}" for word, n in report.top_formal]
    if report.top_repeats:
        lines += ["", "被重复最多的整句"]
        lines += [f"  ×{n}  {sentence}" for sentence, n in report.top_repeats]
    return "\n".join(lines)


# ── 扎根漂移 ───────────────────────────────────────────────────────────────


async def measure_drift(
    dump: Path,
    gateway: object,
    *,
    sample: int,
    concurrency: int,
    scene: Optional[Scenario] = None,
) -> Tuple[float, int]:
    """拿导出的台词去跑分类器，看"扎根"的判定有没有漂。

    这是这次改造最大的隐藏风险：变体让老陈吐出更多具体细节（八千、老王、
    腊月），玩家因此更容易引用到他说过的东西 → 扎根率上移 → 胜率跟着上移。
    而 expert 47.9% 这条线是评审关的核心资产（docs/TECH-DESIGN.md §9.4）。

    玩家那一侧的话在两份 dump 里是同一批，所以扎根率的差异只能来自老陈——
    这正是要量的东西。第 1 轮跳过：它的上下文是开场白，而开场白不在 dump 里。

    **两份 dump 必须是同一个场景的。** 场景一换，玩家那一侧的话也换了，
    差异里就混进了"问的问题不同"这个变量，这个数就不再是漂移。
    dump 自带 `sid`，这里以它为准；旧的 dump 没有这个字段，落到 `scene`。
    """
    from app.classify import parse_classification

    rows = [json.loads(line) for line in dump.read_text(encoding="utf-8").splitlines()]
    scene = scene or DEFAULT
    if not rows:
        raise ValueError(f"{dump} 是空的，没有台词可以拿去判扎根")
    sids = {row.get("sid", scene.id) for row in rows}
    if len(sids) > 1:
        raise ValueError(f"这份 dump 混了多个场景 {sorted(sids)}，扎根漂移没法比")
    routes = {r.id: r for r in load_routes(sid=sids.pop())}
    replies = {(r["route"], r["run"], r["round"]): r["sentences"] for r in rows}

    pairs = []
    for row in rows:
        if row["round"] < 2:
            continue
        previous = replies.get((row["route"], row["run"], row["round"] - 1))
        if not previous:
            continue
        pairs.append(
            (routes[row["route"]].utterances[row["round"] - 1], "".join(previous))
        )
    pairs = pairs[:: max(1, len(pairs) // sample)][:sample]

    gate = asyncio.Semaphore(concurrency)

    async def one(utterance: str, context: str) -> Optional[bool]:
        """一条判定。**必须自己扛住网关抖动**——这一处漏了很久。

        两个 eval 的主路径都重试两次、失败的样本从指标里剔除，唯独这里是
        裸 await 挂在 `asyncio.gather` 上：**一次瞬时 401 就让整批作废**，
        200 条判定一条都拿不到。8-22 实测撞上两次（`ip restriction!` 与
        「该令牌状态不可用」），第二次正是在这儿炸的。

        失败返回 None 而不是 False：False 是"判为未扎根"，会把扎根率算低，
        而这个数是用来做前后对比的——凭空压低它等于伪造一个"没有漂移"。
        """
        for attempt in range(ACT_RETRIES + 1):
            try:
                async with gate:
                    raw = await gateway.classify(  # type: ignore[attr-defined]
                        utterance=utterance, history=(), opening=context
                    )
                parsed = parse_classification(raw)
                return bool(parsed and parsed.grounded)
            except Exception as exc:  # noqa: BLE001 - 瞬时故障不该毁掉整批
                if attempt == ACT_RETRIES:
                    print(f"  ! 一条判定放弃：{type(exc).__name__}", flush=True)
                    return None
                await asyncio.sleep(_backoff_for(exc, attempt))
        return None

    results = await asyncio.gather(*(one(u, c) for u, c in pairs))
    verdicts = [v for v in results if v is not None]
    dropped = len(results) - len(verdicts)
    if dropped:
        print(f"{dropped} 条重试三次仍失败，已从指标中剔除", flush=True)
    return (sum(verdicts) / len(verdicts) if verdicts else 0.0), len(verdicts)


def main() -> int:
    # GBK 终端上打印不出来的字符降级成 ?，而不是让整个跑批崩掉
    guard()
    parser = argparse.ArgumentParser(description="演绎跑批（会真实调用模型）")
    parser.add_argument("--routes-file", type=Path, default=DEFAULT_ROUTES_PATH)
    parser.add_argument(
        "--scenario",
        default=DEFAULT.id,
        choices=[s.id for s in SCENARIOS],
        help="跑哪个场景。一次只跑一个，指标不跨场景合并",
    )
    parser.add_argument("--routes", default="", help="只跑这些路线，逗号分隔")
    parser.add_argument("--runs", type=int, default=20, help="每条路线跑几遍")
    parser.add_argument("--rounds", type=int, default=0, help="只跑前 N 轮，0 表示全部")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--dump", type=Path, default=None, help="把台词导出到 jsonl")
    parser.add_argument(
        "--drift",
        type=Path,
        default=None,
        help="不跑演绎，改为拿一份导出的台词去跑分类器，看扎根率有没有漂",
    )
    parser.add_argument("--drift-sample", type=int, default=200, help="漂移抽样条数")
    args = parser.parse_args()

    # 延迟导入：网关会连带加载配置（缺 STATE_SIGNING_SECRET 即拒绝启动），
    # 而 summarize / check_thresholds 这些纯函数不该被这条约束拖住
    from app.gateway import ModelGateway

    scene = scenario_for(args.scenario)

    if args.drift:
        # 单独一条路：它花的是分类请求（短、便宜），不重跑演绎。
        # 两份 dump 各跑一次，比的是同一批玩家发言在不同老陈面前的扎根率
        rate, n = asyncio.run(
            measure_drift(
                args.drift,
                ModelGateway(),
                sample=args.drift_sample,
                concurrency=args.concurrency,
                scene=scene,
            )
        )
        print(f"{args.drift.name}：抽 {n} 条，判为扎根 {rate:.1%}")
        print("拿改造前后两份 dump 各跑一次再比。扎根率明显上移，")
        print("意味着老陈吐出的具体细节变多、玩家更容易接住——胜率会跟着上移，")
        print("那条线是评审关的资产（docs/TECH-DESIGN.md §9.4），届时要重跑 balance_sim。")
        return 0

    routes = load_routes(args.routes_file, sid=scene.id)
    if args.routes:
        wanted = {r.strip() for r in args.routes.split(",")}
        routes = tuple(r for r in routes if r.id in wanted)
    if not routes:
        print("没有匹配的路线", file=sys.stderr)
        return 2

    turns = args.rounds or len(routes[0].utterances)
    calls = len(routes) * args.runs * turns
    print(
        f"场景 {scene.id}（{scene.name} · {scene.kind}）：",
        f"{len(routes)} 条路线 × {args.runs} 遍 × {turns} 轮 = "
        f"{calls} 次演绎调用，并发 {args.concurrency}…",
        flush=True,
    )
    replies = asyncio.run(
        run(
            routes,
            ModelGateway(),
            runs=args.runs,
            scene=scene,
            rounds=args.rounds,
            concurrency=args.concurrency,
        )
    )

    # **中途换过模型就不许写 dump**（2026-09-03，踩过一次才加的）。
    #
    # 那次是这样：内网网关的 token 在跑批跑到一半时用尽，ADR-0007 的自动降级
    # 把后半批切到了公网模型，而这个脚本**照常把两个模型的台词写进了同一个
    # 文件**——文件里没有任何一个字段说得清哪一行是谁说的。
    #
    # 代价是实打实的：这批语料量的是"老陈演得像不像人"，混了两个模型之后
    # 每一个指标都不成立；而 `baseline-*.jsonl` 不在 git 里，
    # **那次覆盖直接毁掉了上一份可用的基线**，且当时 token 已尽、重跑不了。
    #
    # 一份混模型的语料比没有语料坏得多：没有语料是看得见的空缺，
    # 混模型的语料看起来一切正常，然后静默污染每一个下游指标
    # （act_eval 自己的六道门槛、tools/cue_coverage.py 的线索覆盖率）。
    # 所以这里宁可什么都不写，并且把话说清楚。
    switched = bool(llm_client.status().get("switched"))
    if args.dump and switched:
        st = llm_client.status()
        print(FAIL(
            f"\n跑批中途换过模型（→ {st.get('active_provider')}/{st.get('active_model')}），"
            f"**不写 {args.dump}**。\n"
            f"  原因：{st.get('reason') or '见上面的降级提示'}\n"
            f"  这一批是两个模型混出来的，量演绎质量不成立。"
            f"修好网关后整批重跑；\n"
            f"  已有的 baseline 不在 git 里，覆盖掉就找不回来了。"
        ))
        args.dump = None

    if args.dump:
        with args.dump.open("w", encoding="utf-8") as fh:
            for reply in replies:
                fh.write(
                    json.dumps(
                        {
                            # 场景要留在 dump 里：`--drift` 靠它找回这一批用的
                            # 是哪一份路线，而路线决定玩家那一侧说了什么
                            "sid": scene.id,
                            "route": reply.route,
                            "run": reply.run,
                            "round": reply.round,
                            "sentences": list(reply.sentences),
                            # 原样也留一份：安全层丢掉的东西（英文外漏、括号动作）
                            # 只在这里看得见。少了它，事后想复查一次跑批
                            # 到底漏没漏英文，只能整批重跑
                            "raw": reply.raw,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        print(f"台词已导出到 {args.dump}（人格那一类靠人眼看，指标量不了）")

    report = summarize(replies, args.runs)
    print()
    print(f"场景 {scene.id} · {scene.name}（{scene.kind}）")
    print(format_report(report))
    print()

    failures = check_thresholds(report)
    if failures:
        print("门槛未通过：")
        for f in failures:
            print(f"  {FAIL} {f}")
        return 1
    if FIRST_DUP_CEILING is None:
        print("（首句重复与复述反问的门槛尚未标定，本次只出数字）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
