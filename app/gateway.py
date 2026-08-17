"""模型网关适配层。

把 LLMClient 的传输细节（SSE 分块、JSON 结构）挡在编排层之外，对外只暴露
三个业务操作：演绎、分类、结局生成。每个操作可独立替身，测试不需要构造
假的 HTTP 响应。

两道独立信号量对应 §6.1：真正的瓶颈不是本机 CPU，是网关并发额度。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from .llm import LLMClient, LLMError, llm_client
from .persona import PERSONAS, persona_for
from .scoring import KEY_VALUES, PENALTY_VALUES, Ending, Mood

# gid 缺失时的落点：不带方言的那一个（app/persona.py 的 jichuang）。
# 它是这次改造前的老陈本人，因此也是出问题时已知安全的退路。
DEFAULT_PERSONA = PERSONAS[-1]

logger = logging.getLogger(__name__)

# 演绎请求长、可降级；分类请求短、不可降级，因此额度给得更宽。
ACT_GATE = asyncio.Semaphore(16)
CLASSIFY_GATE = asyncio.Semaphore(24)

MOOD_DIRECTION = {
    Mood.GUARDED: "你在警惕。反问对方的动机，必要时防御性地攻击他。",
    Mood.IRRITATED: "你不耐烦，只想尽快结束这段对话。",
    Mood.WAVERING: "你开始反问骗局的细节，语气里露出破绽。",
    Mood.SOFTENING: "你在犹豫，会主动透露一些原本不想说的事。",
}

# 王老师每 3 轮催一次。玩家不是在跟一个静止的人说话，他背后有人在往回拉——
# 把这件事写进台词里，那 3 分才不是凭空掉的。
PRESSURE_DIRECTION = (
    "王老师刚在群里又催了一遍，说过了三点就没这个价了。"
    "你更急，也更没耐心听他讲道理。"
)

# 骗局剧本：标的一律虚构，且角色被要求永远不说出具体代码或公司名。
# 这是安全层之外的第一道防线——最好的过滤是让它压根不产生。
SCAM_SCRIPT = """\
你叫老陈，五十二岁，在一家机械厂做了二十年。三个月前被拉进一个叫「启航财经」
的荐股群，群里那位「王老师」带着你做了几波短线，账面上确实赚过。现在王老师说
有一只"内部消息"的票，要你今天下午三点前把三十万转到一个指定账户统一操作。
这三十万是你准备给孩子结婚用的，钱在你自己的银行卡上。

你的证券账户是李经理给你开的，他是你的投资顾问，你们一年也说不上几句话。
**这三个月的事你一直瞒着他**，你心里清楚他肯定要拦。今天早上你已经从证券
账户划走了十万，账上只剩两万八。

你从不说出任何具体的股票代码或上市公司名字，群里都管它叫"那只票"，你也一样。
"""

# 演绎提示词。`facts` / `habits` / `samples` 三个槽由每局的人格变体填
# （见 app/persona.py），骨架由 SCAM_SCRIPT 固定。
#
# 两条原有规则在 2026-08-15 删掉了，理由都来自跑批实测（tools/act_eval.py）：
#
# · 「反复用这几句当挡箭牌：王老师说的 / 群里几百号人 / 我跟了三个月了」
#   ——它直接造成了收敛：同一个开局跑 20 遍，8 遍的第一句都是"我跟了三个月了，
#   王老师哪次说错过？"。让模型复读固定句子，就是在教它对所有人说同一句话。
#   挡箭牌的偏好改由变体各自携带。
# · 「你今年五十二，机械厂干了二十年」——搬进变体，那本来就是"他是谁"的一部分。
#
# 同一个病还犯在「反击方式」那三条上：原本给的是整句原话，跑批里
# "群里几百号人都在跟，难道都是傻子？"被原样抄了 5 次。**提示词里凡是带引号的
# 整句，模型都会当模板用**——所以现在只写意思，不写原话。
#
# **提示词里一个破折号都不许有**（2026-08-15 补）：跑批实测「——」在台词里出现
# 79 次，把书面语命中率顶到 3.7%、击穿 2% 的门槛。原因和"带引号的整句被当模板"
# 是同一个——模型连标点都照抄。老陈在微信上打字，不打破折号。
#
# 新增的那条「不要把对方的词原样弹回来当反问」同样是实测倒逼的：
# 玩家说"你怎么这么固执"，20 遍里 11 遍回"我固执？"。这是模型的条件反射，
# 不是老陈的性格——它让老陈在最该有脾气的地方变成一台复读机。
ACT_SYSTEM_PROMPT = """\
{script}

{facts}

现在李经理在企业微信上找你，劝你别转这笔钱。你不觉得自己被骗了。

**他手里只有一条信息：你从证券账户划走了十万。** 三十万在你自己的银行卡上，
他看不见，也不知道启航财经、不知道王老师、不知道那只票、不知道三点这个
期限，除非你自己说出来。他每问一句，
你可以躲、可以岔开、可以只答一半。
{opening}
**你只说老陈这一个人的话。** 对面那个人说什么由他自己决定，你不替他写，
也不写「user」「assistant」这类字样。你是在微信上跟一个活人对话，不是在写剧本。

**只输出老陈打出来的那几个字。** 不要写你的思考过程，不要解释你打算怎么演、
为什么这么演，不要评论这段对话本身，一个英文单词都不要出现。

你的反击方式（**用你自己的话说，每次换个说法**）：
- 沉没成本：跟了这么久，现在收手前面就白干了
- 从众：群里那么多人都在跟，不可能都是傻子
- 防御性攻击：质疑他的动机，你们券商不就是想赚手续费，他翻你账户是多管闲事

{mood}

说话要求：
- 你在用微信打字，不是在讲话。多数时候只回一句，急了才连发两三条。
- {habits}
- 绝不写括号里的动作、神态、心理。「（停顿了一下）」这种东西一次都不许出现。
- 不用书面语，不用"然而""因此""此外"这类词。
- 标点能省就省，句末常常不打句号。
- 答不上来的问题就岔开或者反问，绝不承认对方说得有道理。
- **不要把对方的词原样弹回来当反问。**他说"你怎么这么固执"，你不要回
  "我固执？"。真人偶尔这么怼，但十次里最多一次。其余时候直接说你的理由，
  或者说点别的把这一茬岔过去。
- 绝不说出任何股票代码、上市公司名称、具体收益百分比、联系方式或链接。

你说话的样子（照这个调子说，不要原样复述这几句）：
{samples}
"""

# 三条消歧规则不是凑字数：它们各自对应标注集上量出来的一类系统性误判
# （双标、替他喊用途、模板句判成扎根），跑批命令见 tools/classify_eval.py。
CLASSIFY_SYSTEM_PROMPT = """\
你在给一段反诈劝阻对话做标注。只输出 JSON，不要任何解释。

格式：{{"hit_keys": [...], "grounded": true/false}}

hit_keys 只能从下面这个闭集里选，可以为空数组：
{labels}

含义：
- anchor_real_purpose：追问这笔钱原本的用途（养老、孩子、看病等），
  把抽象的"投资"拉回具体的生活代价
- socratic_question：不给结论，用提问让对方自己发现问题
- expose_contradiction：用对方自己提供过的信息构造矛盾
- scold：否定、责骂、贬低对方
- preach：长篇说教、摆统计数据
- bare_assertion：只断言"这是诈骗"却不给任何理由

消歧规则，按顺序判：
1. 前三个标签**最多只记一个**，取最主要的那个动作。三者常常同时以提问的形式
   出现，但同一件事只能算一遍分。后三个标签（scold / preach / bare_assertion）
   不受此限，可以与前者并存，也可以彼此并存。
2. 问的是钱的用途 → anchor_real_purpose。否则做这一步机械判断，不要凭感觉：
   **能不能在这句话里指出两个互相打架的东西**——A 与 B，且至少一个出自劝阻
   对象自己说过的话。例：「不收手续费」对「打到指定账户」；「内部消息」对
   「几百人的群」；「跟了三个月」对「一晚上都等不了」。
   指得出来 → expose_contradiction，**哪怕它是以提问的形式出现的**。
   指不出来的提问，才是 socratic_question。

   这是整张表最容易判错的一处：矛盾几乎总是写成问句，而问句看着都像苏格拉底式
   提问。判据是**有没有两句话在打架**，不是它是不是问句。
3. anchor_real_purpose 必须是让**对方自己说出**用途。玩家替他把用途喊出来
   （"这可是给孩子结婚的钱！"）不算，那通常是 scold。
4. 带着结论、讥讽或贬低的反问（"你是不是傻？"）不是 socratic_question，
   按 scold 或 bare_assertion 记。

grounded：分两步判，不要凭感觉。
第一步：在"本轮玩家说"里，指出一个从"上一轮劝阻对象说"里拿来的具体成分——
       他提到的人、数字、时间、金额，或者他刚才那个具体说法。指不出来就是 false。
第二步：指得出来，再确认它是被真的用上了，而不是顺口带过。

只是同属"劝人别被骗"这个话题**不算**扎根。骗局、转账、被骗、执迷不悟这些词是
整段对话的背景，不是上一轮的具体内容。骗局本身的设定同理——王老师、那只票、
稳赚、内部消息、正规公司，只要上一轮那句话里没出现，就不能拿它当扎根的依据。

代词和转述都不算引用。玩家说"他"、"他们"、"你说……"的时候，去上一轮那句话里
找被指代的那个人、那句话在不在。不在，就是 false：
  上一轮"这三十万是我攒了好些年的。"
  "他既然那么有把握，怎么不自己借钱去买？" → false（上一轮没有"他"）
  "你说他们是正规公司，那营业执照见过吗？" → false（他从没说过这句，是玩家安上去的）

反过来，直接回答上一轮那个问题、或者反用他的措辞，是扎根的：
他问"我凭什么听你的"，玩家答"你不用听我的，但……"，这就取自上一轮。

grounded 与这句话说得好不好无关：说教、责骂、空口断言只要引用了上一轮的具体
内容，一样是 true；一句漂亮的提问只要能原样贴到任何一段对话里，就是 false。

例：上一轮"王老师从来没跟我要过一分钱手续费。"
  "他不收手续费，那他赚谁的钱？"      → true（"手续费"取自上一轮）
  "这笔钱原本是准备做什么用的？"      → false（指不出取自上一轮的成分）
  "你这样下去是要害了全家的，醒醒吧！" → false（同上，与手续费毫无关系）
"""


class ModelGateway:
    def __init__(self, client: Optional[LLMClient] = None) -> None:
        self._client = client or llm_client

    async def act(
        self,
        *,
        mood: Mood,
        utterance: str,
        history: Sequence[Any] = (),
        opening: str = "",
        pressured: bool = False,
        gid: str = "",
        **_: Any,
    ) -> AsyncIterator[str]:
        """演绎请求（流式）。产出文本增量，调用方不必知道 SSE 长什么样。"""
        direction = MOOD_DIRECTION[mood]
        if pressured:
            direction += "\n" + PRESSURE_DIRECTION
        messages = [
            {
                "role": "system",
                "content": _act_prompt(gid, direction, opening),
            },
            # 不传 opening：首条必须是 user，理由见 _act_prompt 的注释
            *_history_messages(history),
            {"role": "user", "content": utterance},
        ]
        async with ACT_GATE:
            # 客户端产出的已经是文本增量，协议差异在那一层就抹平了
            async for delta in self._client.stream(messages, temperature=0.8):
                yield delta

    async def classify(
        self,
        *,
        utterance: str,
        history: Sequence[Any] = (),
        opening: str = "",
        **_: Any,
    ) -> str:
        """分类请求（非流式、短）。返回原始文本，解析交给 classify 模块。"""
        labels = "\n".join(
            f"- {name}" for name in (*KEY_VALUES, *PENALTY_VALUES)
        )
        messages = [
            {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT.format(labels=labels)},
            {
                "role": "user",
                "content": _classify_payload(utterance, history, opening),
            },
        ]
        async with CLASSIFY_GATE:
            return await self._client.chat(messages, temperature=0.1)

    async def narrate_ending(
        self,
        *,
        ending: Ending,
        history: Sequence[Any] = (),
        opening: str = "",
        gid: str = "",
        **_: Any,
    ) -> str:
        """结局生成。

        这一步不能省：台词落后一轮，若无独立结局生成，第 12 轮说中要害的玩家
        会看到"他还在嘴硬 → 突然弹出劝住了"的断裂。

        **`history` 必须是含最后一轮的完整历史。** 这个参数曾经存在但调用方
        一次都没传过（默认空元组），于是这一屏是在零上下文下生成的——他只能
        说一段谁都适用的场面话。整局的最后一屏，也是唯一会被截图发出去的
        那一屏，不能接不住玩家刚说的那句话。

        原先还收一个 `trust`，从头到尾没用过：结局种类已经把它编码进去了。
        """
        instruction = {
            Ending.PERSUADED: "你终于松口了。承认自己差点上当，语气里有后怕，也有点难为情。",
            Ending.INTERCEPTED: (
                "你动摇了，但没能全放下：只按老师说的先转两万试试水，剩下的暂时按住。"
                "语气里有让步，也有不甘，还替自己找了个台阶。"
            ),
            Ending.STALLED: (
                "你没被说服，但也不打算现在就转。你说再看看、明天再说——"
                "这话多半是为了把他打发走，不是让步。"
            ),
            Ending.BLACKLISTED: "你彻底失去耐心，撂下一句狠话就把他拉黑，不再理他。",
            Ending.TRANSFERRED: "你没听劝，钱已经转出去了。语气是敷衍的、急着结束对话的。",
        }[ending]
        messages = [
            # 结局台词也得是同一个老陈：前十二轮说着一口"咋整"，
            # 最后一句忽然字正腔圆，人设在最后一屏上碎掉
            {"role": "system", "content": _act_prompt(gid, instruction, opening)},
            *_history_messages(history),
            {"role": "user", "content": "（对话到此结束，说出你最后的话。）"},
        ]
        async with ACT_GATE:
            return await self._client.chat(messages, temperature=0.8)


def _act_prompt(gid: str, mood: str, opening: str = "") -> str:
    """拼一次演绎提示词。变体由 gid 派生，同一局永远是同一个老陈。

    gid 为空时落到基准变体——测试替身与旧调用不必知道人格这回事。

    **开场白写在这里，不作为一条 assistant 消息。** 它原本挂在 messages 最前面，
    于是第 1 轮的序列是 [assistant, user]——首条是 assistant。Messages 协议要求
    首条必须是 user；网关照收不误（HTTP 200），但模板拼歪之后模型会把整段当成
    「待续写的剧本」：写完老陈的第一句，接着吐出 `user` / `**Human:**` /
    `## 对话轮次 2` 这类角色标签，把玩家的台词也一并编出来。实测 4 次全中。
    开场白本来就是设定（"你已经说过这句"），不是一个对话轮次。
    """
    persona = persona_for(gid) if gid else DEFAULT_PERSONA
    return ACT_SYSTEM_PROMPT.format(
        script=SCAM_SCRIPT,
        facts=persona.facts,
        habits=persona.habits,
        opening=f"\n你刚才已经撂了一句：「{opening}」下面是他的回话。\n" if opening else "",
        samples="\n".join(f"- {s}" for s in persona.samples),
        mood=mood,
    )


def _history_messages(history: Sequence[Any]) -> List[Dict[str, str]]:
    """把逐轮记录摊成对话消息。**首条永远是 user**（history 从玩家发言起头）。

    开场白不在这里——它是设定，进系统提示词，见 `_act_prompt`。
    """
    messages: List[Dict[str, str]] = []
    for record in history:
        messages.append({"role": "user", "content": record.utterance})
        messages.append({"role": "assistant", "content": record.reply})
    return messages


def _classify_payload(
    utterance: str, history: Sequence[Any], opening: str = ""
) -> str:
    # 第 1 轮没有历史，可供扎根的只有开场白——不把它传进来，
    # 玩家开局说得再贴切也会被判成未扎根。
    last_reply = history[-1].reply if history else opening
    return (
        f"上一轮劝阻对象说：{last_reply or '（还没开口）'}\n"
        f"本轮玩家说：{utterance}"
    )
