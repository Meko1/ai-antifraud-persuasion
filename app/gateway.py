"""模型网关适配层。

把 LLMClient 的传输细节（SSE 分块、JSON 结构）挡在编排层之外，对外只暴露
三个业务操作：演绎、分类、结局生成。每个操作可独立替身，测试不需要构造
假的 HTTP 响应。

两道独立信号量对应 §6.1：真正的瓶颈不是本机 CPU，是网关并发额度。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from .llm import LLMClient, LLMError, llm_client
from .scoring import KEY_VALUES, PENALTY_VALUES, Ending, Mood

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

# 骗局剧本：标的一律虚构，且角色被要求永远不说出具体代码或公司名。
# 这是安全层之外的第一道防线——最好的过滤是让它压根不产生。
SCAM_SCRIPT = """\
你叫老陈，五十二岁，在一家机械厂做了二十年。三个月前被拉进一个叫「启航投顾」
的荐股群，群里那位「李老师」带着你做了几波短线，账面上确实赚过。现在李老师说
有一只"内部消息"的票，要你今天下午三点前把三十万转到一个指定账户统一操作。
这三十万是你准备给孩子结婚用的。

你从不说出任何具体的股票代码或上市公司名字——群里都管它叫"那只票"，你也一样。
"""

ACT_SYSTEM_PROMPT = """\
{script}

现在群里另一个人私聊你，劝你别转这笔钱。你跟他不熟，只知道是同一个群的。
你不觉得自己被骗了。

你的反击方式：
- 沉没成本："我都跟了三个月了，现在停手前面不都白干了？"
- 从众："群里几百号人都在跟，难道都是傻子？"
- 防御性攻击：反过来质疑对方的动机和资格

{mood}

说话要求：
- 开口就是短句，一句一句说，不要长篇大论
- 用口语，像在微信上打字，不要书面语
- 绝不说出任何股票代码、上市公司名称、具体收益百分比、联系方式或链接
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
2. 问的是钱的用途 → anchor_real_purpose。指出对方话里的矛盾 → expose_contradiction。
   两者都不是的提问，才是 socratic_question。
3. anchor_real_purpose 必须是让**对方自己说出**用途。玩家替他把用途喊出来
   （"这可是给孩子结婚的钱！"）不算，那通常是 scold。
4. 带着结论、讥讽或贬低的反问（"你是不是傻？"）不是 socratic_question，
   按 scold 或 bare_assertion 记。

grounded：分两步判，不要凭感觉。
第一步：在"本轮玩家说"里，指出一个从"上一轮劝阻对象说"里拿来的具体成分——
       他提到的人、数字、时间、金额，或者他刚才那个具体说法。指不出来就是 false。
第二步：指得出来，再确认它是被真的用上了，而不是顺口带过。

只是同属"劝人别被骗"这个话题**不算**扎根。骗局、转账、被骗、执迷不悟这些词是
整段对话的背景，不是上一轮的具体内容。骗局本身的设定同理——李老师、那只票、
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

例：上一轮"李老师从来没跟我要过一分钱手续费。"
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
    ) -> AsyncIterator[str]:
        """演绎请求（流式）。产出文本增量，调用方不必知道 SSE 长什么样。"""
        messages = [
            {
                "role": "system",
                "content": ACT_SYSTEM_PROMPT.format(
                    script=SCAM_SCRIPT, mood=MOOD_DIRECTION[mood]
                ),
            },
            *_history_messages(history, opening),
            {"role": "user", "content": utterance},
        ]
        async with ACT_GATE:
            async for raw in self._client.stream(messages, temperature=0.8):
                delta = _extract_delta(raw)
                if delta:
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
        self, *, ending: Ending, trust: int, history: Sequence[Any] = ()
    ) -> str:
        """结局生成。

        这一步不能省：台词落后一轮，若无独立结局生成，第 12 轮说中要害的玩家
        会看到"他还在嘴硬 → 突然弹出劝住了"的断裂。
        """
        instruction = {
            Ending.PERSUADED: "你终于松口了。承认自己差点上当，语气里有后怕，也有点难为情。",
            Ending.BLACKLISTED: "你彻底失去耐心，撂下一句狠话就把他拉黑，不再理他。",
            Ending.TRANSFERRED: "你没听劝，钱已经转出去了。语气是敷衍的、急着结束对话的。",
        }[ending]
        messages = [
            {
                "role": "system",
                "content": ACT_SYSTEM_PROMPT.format(
                    script=SCAM_SCRIPT, mood=instruction
                ),
            },
            *_history_messages(history),
            {"role": "user", "content": "（对话到此结束，说出你最后的话。）"},
        ]
        async with ACT_GATE:
            return await self._client.chat(messages, temperature=0.8)


def _history_messages(
    history: Sequence[Any], opening: str = ""
) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    if opening:
        # 开场白是角色说的第一句，模型必须看见，否则它会重新自我介绍一遍
        messages.append({"role": "assistant", "content": opening})
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


def _extract_delta(raw: str) -> str:
    """从一块流式响应里取出文本增量。结构异常一律当成空块跳过。"""
    try:
        payload = json.loads(raw)
        return payload["choices"][0]["delta"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return ""
