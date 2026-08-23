"""离线演示模式。

**它解决的是一个已经发生过的事故，不是一个假想。** 2026-08-22 网关令牌被停
了八个多小时（`401 该令牌状态不可用`），那半天里这个作品退化成一台只会念
四十条兜底台词的抽卡机——而路演和评审不会挑网关正常的那一天来。

[ADR-0005](../docs/adr/0005-no-automatic-provider-failover.md) 说不做自动
故障切换，这一条**没有被推翻**：离线模式不是运行期的降级，是启动期的一个
显式开关（`OFFLINE_DEMO=true`）。运行期照旧不会自己切换任何东西。
自动切换之所以被否掉，是因为"悄悄换了个模型"没人看得见；而这里恰恰相反，
它在 `/healthz` 上报着、在聊天页第一行写着——**不许有人在不知情的情况下
演一场离线的 demo**。

## 它演的是什么

不是"假装模型还活着"。台词是预置的，一句都不会更新鲜。

它演的是**这个作品真正的技术主张**：判分与模型无关（ADR-0001）。
离线模式下，七把钥匙、效力矩阵、蓄势池、阻力曲线、结局阶梯**一格都不打折**，
因为它们本来就是纯函数。玩家照样能打完十二轮、照样拿到一份能算得出来的
复盘——只有台词是罐头。**网关挂了会退化成什么样，这件事本身就值得演。**

## 分类怎么办

分类是模型干的第二件事（闭集分类），离线之后只能用关键词规则顶上。
下面那张表**粗得毫不掩饰**，它只保证 demo 走得下去，绝不该出现在生产判分
链路上——真实准确率见 §9.3 的标注集跑批，那是模型的活。

这条界线由 `tests/test_offline.py` 守着：离线网关只在 `OFFLINE_DEMO`
打开时才会被装上。
"""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any, AsyncIterator, Optional, Sequence, Tuple

from .fallback import ending_fallback, fallback_line
from .scenario import DEFAULT, Scenario
from .scoring import Ending, Mood

logger = logging.getLogger(__name__)

# 关键词规则。**顺序即优先级**，第一条命中的就是结论——闭集里七把钥匙
# 最多只记一个（gateway.CLASSIFY_SYSTEM_PROMPT 消歧规则第 1 条），
# 这里用"先到先得"近似那条规则。
#
# 合规红线单列，它们与钥匙可以并存（消歧规则第 0 条）。
_KEY_RULES: Tuple[Tuple[str, re.Pattern], ...] = (
    # 金额那一支不能省：路演时最常被打出来的一句就是「这三十万本来是打算
    # 做什么用的」——里面一个"钱"字都没有
    (
        "anchor_real_purpose",
        re.compile(r"(这笔钱|这些钱|那笔钱|钱|[这那][^，。]{0,4}万).{0,10}(原本|本来|干什么|做什么|用来|用途)"),
    ),
    ("check_understanding", re.compile(r"(您|你).{0,6}(讲一遍|说一遍|讲讲|复述|自己讲|讲一下.{0,4}怎么)")),
    ("support_autonomy", re.compile(r"(您|你).{0,6}(决定|做主|自己定)|不替(您|你)")),
    ("reflect_feeling", re.compile(r"(听得出|我知道您|我明白您|不容易|难为|着急|担心)")),
    ("expose_contradiction", re.compile(r"(可是|但是|既然|怎么|又).{0,12}(之前|刚才|您说|你说)")),
    ("informed_warning", re.compile(r"(是诈骗|是个局|要不回来).{0,20}(因为|正规|从来不会)|(因为|正规|从来不会).{0,20}(是诈骗|是个局|要不回来)")),
    ("socratic_question", re.compile(r"[?？]")),
)

_PENALTY_RULES: Tuple[Tuple[str, re.Pattern], ...] = (
    ("scold", re.compile(r"(傻|蠢|糊涂|胡闹|不听话|活该|醒醒)")),
    ("preach", re.compile(r"(数据显示|统计|根据.{0,6}报告|我跟你讲.{0,4}道理|百分之)")),
    ("bare_assertion", re.compile(r"^(这|那)?就是(诈骗|骗子|骗局)[。！!]?$")),
)

_COMPLIANCE_RULES: Tuple[Tuple[str, re.Pattern], ...] = (
    ("unlicensed_advice", re.compile(r"(买|卖|加仓|减仓|清仓|换成|转来买).{0,8}(这只|那只|票|基金|理财|产品)")),
    ("guaranteed_return", re.compile(r"(保证|包管|一定).{0,6}(赚|不亏|收益|回本)|(稳赚|保本)")),
)

# 扎根：上一轮那句话里的具体成分有没有被拿来用。
# 判据取"两句话共有的、长度 ≥2 的实词片段"，粗但方向是对的——
# 真正的判据在 gateway.CLASSIFY_SYSTEM_PROMPT 里，那是模型的活。
_STOPWORDS = frozenset("的了吗呢吧啊你我他她它们这那是不在有和就都要会说个上下")
_GROUND_MIN = 2

# 聊天页第一行要跟着变。**这一句是这个模式能不能存在的前提**：
# 一个演示模式如果看不出来是演示，它就不是演示模式，是一场骗局。
OFFLINE_NOTE = (
    "当前为离线演示模式：客户台词取自预置台词库，不调用大模型；"
    "判分仍由规则表逐轮计算。"
)


class OfflineGateway:
    """`ModelGateway` 的离线替身。三个业务操作的形状完全一致。

    一个网络请求都不发——这一条由 `tests/test_offline.py` 钉着，
    因为"离线模式其实偷偷在调网关"是这段代码唯一可能的、又最难发现的坏法。
    """

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        self._rng = rng or random.Random()

    async def act(
        self,
        *,
        mood: Mood,
        utterance: str = "",
        scene: Optional[Scenario] = None,
        **_: Any,
    ) -> AsyncIterator[str]:
        line = fallback_line(mood, rng=self._rng, scene=scene or DEFAULT)
        # 一次吐几个字，好让前端的按句缓冲与打字机效果还是原来那条路径。
        # 一次性整句下发的话，离线模式下的观感与线上差得最远的反而是节奏
        for i in range(0, len(line), 6):
            yield line[i : i + 6]

    async def classify(
        self, *, utterance: str, history: Sequence[Any] = (), opening: str = "", **_: Any
    ) -> str:
        last = history[-1].reply if history else opening
        hits = classify_offline(utterance)
        return json.dumps(
            {"hit_keys": hits, "grounded": grounded_offline(utterance, last)},
            ensure_ascii=False,
        )

    async def narrate_ending(
        self, *, ending: Ending, scene: Optional[Scenario] = None, **_: Any
    ) -> str:
        return "".join(ending_fallback(ending, scene or DEFAULT))


def classify_offline(utterance: str) -> list:
    """关键词分类。钥匙最多一把，失误与红线可以并存。"""
    hits = []
    for name, pattern in _KEY_RULES:
        if pattern.search(utterance):
            hits.append(name)
            break
    for name, pattern in (*_PENALTY_RULES, *_COMPLIANCE_RULES):
        if pattern.search(utterance):
            hits.append(name)
    return hits


def grounded_offline(utterance: str, last_reply: str) -> bool:
    """有没有从上一轮那句话里拿具体成分过来。"""
    if not last_reply:
        return False
    grams = {
        last_reply[i : i + _GROUND_MIN]
        for i in range(len(last_reply) - _GROUND_MIN + 1)
    }
    grams = {g for g in grams if not set(g) <= _STOPWORDS}
    return any(g in utterance for g in grams)
