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

你正在跟一个陌生人对话，他试图劝你别转这笔钱。你不觉得自己被骗了。

你的反击方式：
- 沉没成本："我都跟了三个月了，现在停手前面不都白干了？"
- 从众："群里几百号人都在跟，难道都是傻子？"
- 防御性攻击：反过来质疑对方的动机和资格

{mood}

说话要求：
- 开口就是短句，一句一句说，不要长篇大论
- 用口语，像真人打电话，不要书面语
- 绝不说出任何股票代码、上市公司名称、具体收益百分比、联系方式或链接
"""

CLASSIFY_SYSTEM_PROMPT = """\
你在给一段反诈劝阻对话做标注。只输出 JSON，不要任何解释。

格式：{{"hit_keys": [...], "grounded": true/false}}

hit_keys 只能从下面这个闭集里选，可以为空数组：
{labels}

含义：
- anchor_real_purpose：让对方说出这笔钱原本的用途（养老、孩子、看病等），
  把抽象的"投资"拉回具体的生活代价
- socratic_question：不给结论，用提问让对方自己发现问题
- expose_contradiction：用对方自己提供过的信息构造矛盾
- scold：否定、责骂、贬低对方
- preach：长篇说教、摆统计数据
- bare_assertion：只断言"这是诈骗"却不给任何理由

grounded：这次发言是否确实回应了对话里出现过的具体内容。
照搬通用模板、复读攻略句式的，一律 false。
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
            Ending.BLACKLISTED: "你彻底失去耐心，撂下一句狠话就挂断，不再理他。",
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
