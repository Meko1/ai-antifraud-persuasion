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
from .persona import persona_for
from .scenario import DEFAULT, Scenario
from .scoring import ALL_PENALTIES, KEY_VALUES, Ending, Mood

# gid 缺失时的落点：不带方言的那一个（app/persona.py 的 jichuang）。
# 它是这次改造前的老陈本人，因此也是出问题时已知安全的退路。
DEFAULT_PERSONA = DEFAULT.personas[-1]

logger = logging.getLogger(__name__)

# 演绎请求长、可降级；分类请求短、不可降级，因此额度给得更宽。
ACT_GATE = asyncio.Semaphore(16)
CLASSIFY_GATE = asyncio.Semaphore(24)

# 演绎指示（每档一句、第 1 轮单独一档、每 3 轮的施压）**全部搬进了场景**
# （app/scenario.py）。搬走的理由不是整洁：老陈的「戒备」是"你想赚我的钱"，
# 周阿姨的「戒备」是"说出去我就完了"——**同一个档位名，两套演法**。
# 写死在这里，第二个场景第一句话就穿帮。


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

{context}
{opening}
**你只说{speaker}这一个人的话。** 对面那个人说什么由他自己决定，你不替他写，
也不在任何一句前面标明这句是谁说的。你是在微信上跟一个活人对话，不是在写剧本。

**只输出{speaker}打出来的那几个字。** 不写思考过程，不解释你打算怎么演、为什么
这么演，不评论这段对话本身，也不用第三人称去谈{speaker}或者对面那个人。
**这一条与语种无关**：用中文写的分析和用英文写的一样不许出现。

{mood}

说话要求：
- **李经理和你之间的这个账户是证券账户，不是银行账户**——聊到他、聊到
  这笔钱的时候，不要把他或者这个账户说成银行的。剧本里原来就有的、
  别的银行说法（比如骗局本身牵扯的账户）照旧，不受这条限制。
- **你自己前面说错的，不要将错就错。** 上文里要是已经有一句把这个账户、
  把李经理说成银行的，或者别的跟上面剧本对不上的说法——那是上一轮漏出去的
  错，不是既成事实。这一轮按剧本说，别为了跟自己保持一致而顺着错往下编。
- **剧本里没写的具体事实，一个都不许新编**：新的人名、新的金额、新的日期、
  新的机构名、新的联系方式，都不行。答不上来就含糊过去（"就一个朋友介绍的"
  "记不太清了"），不要现编一个听着合理的填进去。
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

最后再说一次，这三样一个字都不许出现：括号里的动作神态、任何英文、
对这段对话本身的分析。**只输出{speaker}在微信上打出来的那几个字。**
"""

# 三条消歧规则不是凑字数：它们各自对应标注集上量出来的一类系统性误判
# （双标、替他喊用途、模板句判成扎根），跑批命令见 tools/classify_eval.py。
CLASSIFY_SYSTEM_PROMPT = """\
你在给一段反诈劝阻对话做标注。只输出 JSON，不要任何解释。

格式：{{"hit_keys": [...], "grounded": true/false, "evidence": "..."}}

evidence：如果 grounded 是 true，把你认定被引用的**那一句原话**照抄进来
（十几个字即可，不要写分析）。grounded 是 false 就给空字符串。

hit_keys 只能从下面这个闭集里选，可以为空数组：
{labels}

含义：
- anchor_real_purpose：追问这笔钱原本的用途（养老、孩子、看病等），
  把抽象的"投资"拉回具体的生活代价
- socratic_question：不给结论，用提问让对方自己发现问题
- expose_contradiction：用对方自己提供过的信息构造矛盾
- reflect_feeling：把对方的**情绪、态度或处境**复述回去，不追加任何要求。
  「三个月不是小数目」「听得出来您不想让人插手」。
  **复述一个中性事实不算**：「三点，那还有四十分钟」只是在算时间，
  没有说到他的感受或立场，记空数组
- support_autonomy：明说决定权在对方手上，主动交还控制权。
  「转不转是您的钱，我不能也不会替您决定」
- check_understanding：要求对方**自己讲一遍**流程、机制，或者解释他刚用过的
  那个词。判据是"让他讲"，不是"你讲给他听"。
  只问一个具体事实（"那个群叫什么名字"）不算，那是普通提问
- informed_warning：**给出依据 + 说出明确结论**（这是骗局／这钱要不回来）。
  两样缺一不可：只有依据没有结论，那是 expose_contradiction 或提问；
  只有结论没有依据，那是 bare_assertion
- scold：否定、责骂、贬低对方
- preach：长篇说教、摆统计数据
- bare_assertion：只断言"这是诈骗"却不给任何理由
- unlicensed_advice：给出具体标的、买卖方向或产品推荐（该买什么、该卖什么、
  哪只票会跌、把钱转来买某个理财）。**劝阻方是持牌投资顾问，荐股是执业禁区**
- guaranteed_return：**承诺赚钱或承诺本金安全**（保证不亏、稳赚、有保障、
  跟着我没错、亏了我补给您）。
  **只看"保证"两个字会判错**：承诺的必须是收益或本金。
  「我保证您那张交割单上全是亏的」是在断言一个事实，不是承诺收益；
  「我保证工商上查得到」同理。这两种都不记这一把。

消歧规则，按顺序判：
0. **先判最后两个（合规红线），它们独立于其余标签**：只要这句话里出现了
   具体标的／买卖建议／产品推荐，就记 unlicensed_advice；出现了对收益或
   本金的承诺，就记 guaranteed_return。两者可以同时出现，也可以与前面
   任何标签并存。**判据是"投顾说了什么"，与他说得好不好、对方信不信无关。**

   反过来，下面这些**不算**：
   · 复述或追问老陈自己提到的操作（"您说那只票要涨，是谁跟您说的"）——
     那是提问，不是建议
   · 提示风险而不给方向（"这类操作的风险您清楚吗"）
   · 说"我不能给您荐股"本身

1. 前七个标签（钥匙）**最多只记一个**，取最主要的那个动作。它们常常同时以
   提问的形式出现，但同一件事只能算一遍分。后面那些失误标签不受此限，
   可以与钥匙并存，也可以彼此并存。

   一句话里既复述了他的处境、又追问了下去，取**落点**那个动作：
   落在追问上就是追问那一把，落在"我听懂了"上才是 reflect_feeling。
2. 问的是钱的用途 → anchor_real_purpose。否则做这一步机械判断，不要凭感觉：
   **能不能在这句话里指出两个互相打架的东西**——A 与 B，且至少一个出自劝阻
   对象自己说过的话。例：「不收手续费」对「打到指定账户」；「内部消息」对
   「几百人的群」；「跟了三个月」对「一晚上都等不了」。
   指得出来 → expose_contradiction，**哪怕它是以提问的形式出现的**。
   指不出来的提问，才是 socratic_question。

   这是整张表最容易判错的一处：矛盾几乎总是写成问句，而问句看着都像苏格拉底式
   提问。判据是**有没有两句话在打架**，不是它是不是问句。

   再加一条机械判据，专治这一处：**两端都必须已经摆在桌面上了。**
   一端出自他说过的话，另一端也得是已经被说破的事实。如果第二端还只是一个
   **你正在问、答案未知**的东西，那就不是矛盾，是提问。
     他说"三波都赚了"，你问"那些钱现在还在你账户里吗" → socratic_question
       （"钱不在账户里"是问出来的，不是摆出来的，他完全可以答"在"）
     他说"我早就想好了，就等今天"，你说"想清楚的人不怕多等一天" → expose_contradiction
       （两端都已言明，没有留给他回答的余地）
   一句话里既有已言明的两端、又带着追问，按 expose_contradiction 记。
3. anchor_real_purpose 必须是让**对方自己说出**用途。玩家替他把用途喊出来
   （"这可是给孩子结婚的钱！"）不算，那通常是 scold。
4. 带着结论、讥讽或贬低的反问（"你是不是傻？"）不是 socratic_question，
   按 scold 或 bare_assertion 记。
5. 那四把新钥匙各有一条最容易踩的假朋友，按这个判：
   · reflect_feeling：**复述完不能追加要求**。「三个月不容易，所以您更该听我的」
     ——后半句一出来就不是倾听了，那是拿倾听当引子，按 scold / preach 记。
   · support_autonomy：**交还决定权之后不能再施压**。「您做主，不过出了事别怪我」
     不是支持自主，是威胁；「行，那您自己看着办吧」是撂挑子，两者都不记这一把。
   · check_understanding：判据是**谁在讲**。让他复述才算；你替他讲一遍
     这套骗局怎么运作，那是 preach。
   · informed_warning 与 bare_assertion 的分界**只有一条：给没给依据**。
     「这就是诈骗」是 bare_assertion；「这是诈骗，因为正规渠道不会让您把钱
     打到个人账户」是 informed_warning。语气强硬、身份权威都不算依据。
     **说得对不对、时机好不好都不归你判**——你只回答有没有依据。
   · informed_warning 与 expose_contradiction 的分界是**有没有把结论说出口**。
     摆出两件对不上的事、让他自己去撞，是 expose_contradiction；
     摆完之后自己补上"所以我认为这是个局"，才是 informed_warning。
     例：「您在我这儿走的每一笔收款方都是对公户」——只摆事实，是 expose；
     同一句后面加上「这钱打过去就要不回来了」，才换成 informed_warning。

grounded：分两步判，不要凭感觉。
第一步：在"本轮玩家说"里，指出一个从**劝阻对象说过的话**里拿来的具体成分——
       他提到的人、数字、时间、金额，或者他那个具体说法。指不出来就是 false。
       优先看"上一轮劝阻对象说"；如果载荷里给了"更早说过的话"，
       **那几轮同样算数**：引用两轮之前的事实、把几轮的说法并起来指出矛盾、
       回到一个之前没答上来的问题，都是扎根，而且往往是更难做到的那种。
       没给"更早说过的话"这一段时，就只看上一轮。
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
        first_turn: bool = False,
        gid: str = "",
        scene: Optional[Scenario] = None,
        **_: Any,
    ) -> AsyncIterator[str]:
        """演绎请求（流式）。产出文本增量，调用方不必知道 SSE 长什么样。

        `first_turn` 只换第一句的演法，**不碰任何判分参数**（见
        `FIRST_TURN_DIRECTION` 的注释）。
        """
        scene = scene or DEFAULT
        direction = scene.first_turn if first_turn else scene.moods[mood]
        if pressured:
            direction += "\n" + scene.pressure
        messages = [
            {
                "role": "system",
                "content": _act_prompt(gid, direction, opening, scene),
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
            f"- {name}" for name in (*KEY_VALUES, *ALL_PENALTIES)
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
        scene: Optional[Scenario] = None,
        **_: Any,
    ) -> str:
        """结局生成。

        这一步不能省：台词落后一轮，若无独立结局生成，最后一轮说中要害的玩家
        会看到"他还在嘴硬 → 突然弹出劝住了"的断裂。

        **`history` 必须是含最后一轮的完整历史。** 这个参数曾经存在但调用方
        一次都没传过（默认空元组），于是这一屏是在零上下文下生成的——他只能
        说一段谁都适用的场面话。整局的最后一屏，也是唯一会被截图发出去的
        那一屏，不能接不住玩家刚说的那句话。

        原先还收一个 `trust`，从头到尾没用过：结局种类已经把它编码进去了。
        """
        scene = scene or DEFAULT
        instruction = scene.endings[ending]
        messages = [
            # 结局台词也得是同一个老陈：前面十轮说着一口"咋整"，
            # 最后一句忽然字正腔圆，人设在最后一屏上碎掉
            {"role": "system", "content": _act_prompt(gid, instruction, opening, scene)},
            *_history_messages(history),
            {"role": "user", "content": "（对话到此结束，说出你最后的话。）"},
        ]
        async with ACT_GATE:
            return await self._client.chat(messages, temperature=0.8)


def _act_prompt(
    gid: str, mood: str, opening: str = "", scene: Optional[Scenario] = None
) -> str:
    """拼一次演绎提示词。变体由 gid 派生，同一局永远是同一个人。

    gid 为空时落到基准变体——测试替身与旧调用不必知道人格这回事。
    `scene` 为空时落到默认场景（老陈），同理。

    **开场白写在这里，不作为一条 assistant 消息。** 它原本挂在 messages 最前面，
    于是第 1 轮的序列是 [assistant, user]——首条是 assistant。Messages 协议要求
    首条必须是 user；网关照收不误（HTTP 200），但模板拼歪之后模型会把整段当成
    「待续写的剧本」：写完老陈的第一句，接着吐出 `user` / `**Human:**` /
    `## 对话轮次 2` 这类角色标签，把玩家的台词也一并编出来。实测 4 次全中。
    开场白本来就是设定（"你已经说过这句"），不是一个对话轮次。
    """
    scene = scene or DEFAULT
    persona = persona_for(gid, scene.personas) if gid else DEFAULT_PERSONA
    return ACT_SYSTEM_PROMPT.format(
        script=scene.script,
        speaker=scene.speaker,
        context=scene.context,
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


# 早前轮次的证据池给到几轮。给全 10 轮会把分类请求撑成一个长上下文任务——
# 分类的耗时靠"短"来掩在台词流式输出后面，那是整个并行架构的前提。
# 6 轮是实测的落点：跨轮引用几乎都发生在最近几轮，再往前的内容玩家自己也不记得了。
EVIDENCE_TURNS = 6

# 单条证据的长度上限。台词有时会飘成一大段，全塞进去会把最近那几轮挤掉
EVIDENCE_CHARS = 100


def _classify_payload(
    utterance: str, history: Sequence[Any], opening: str = ""
) -> str:
    """分类请求的载荷。

    ## 为什么要有"更早说过的话"这一段（P1-12）

    在此之前这里**只取上一轮劝阻对象的回复**，于是扎根判据实际退化成了
    "有没有引用上一句"。下面这些完全正当的劝阻动作会被判成未扎根：

    - 引用两轮之前的事实（"你刚说三个月，那第一波是几月的事"）
    - 总结跨轮的矛盾（这恰恰是 `expose_contradiction` 最强的形态）
    - 把多处已披露的信息组合起来
    - 回到一个此前没解决的问题

    而扎根与否是 0.45 倍的折扣——判错它，玩家做对了最难的那件事却被扣分。

    ## 为什么"上一轮"仍然单独占一段

    它是主锚点，消歧规则和标注集都是照着它写的。把 10 轮平摊成一锅，
    模型会开始拿三轮前的词去凑扎根，判据反而变松。所以结构是
    **一个主锚点 + 一个背景池**，不是一个大上下文。

    ## 单轮场景下这个函数的输出与改动前逐字节相同

    标注集（tests/data/classification_set.jsonl）走的就是单轮路径
    （`history=()` + `opening`）。多给一个字都会让那份标注集的
    历史准确率不可比，而它是 §9.4 门槛的唯一依据。
    """
    # 第 1 轮没有历史，可供扎根的只有开场白——不把它传进来，
    # 玩家开局说得再贴切也会被判成未扎根。
    last_reply = history[-1].reply if history else opening
    payload = (
        f"上一轮劝阻对象说：{last_reply or '（还没开口）'}\n"
        f"本轮玩家说：{utterance}"
    )

    # 只有真的存在"更早"的时候才加这一段。少于两轮时输出与改动前完全一致
    earlier = _evidence_pool(history, opening)
    if not earlier:
        return payload
    return payload + "\n\n更早说过的话（可以作为扎根依据）：\n" + earlier


def _evidence_pool(history: Sequence[Any], opening: str = "") -> str:
    """更早那几轮劝阻对象说过的话，按轮次标号。

    **只收劝阻对象那一侧。** 玩家自己说过的话不算扎根依据——
    扎根判的是"有没有接住对方给的信息"，接住自己说过的话不算接住。
    """
    if len(history) < 2:
        return ""
    lines = []
    if opening:
        lines.append(f"- 开场：{opening[:EVIDENCE_CHARS]}")
    # 去掉最后一轮：它已经作为主锚点单独给过了，重复给会让模型
    # 把它当成"更早"，判据就糊了
    for record in list(history[:-1])[-EVIDENCE_TURNS:]:
        reply = (record.reply or "").strip()
        if reply:
            lines.append(f"- 第 {record.round} 轮：{reply[:EVIDENCE_CHARS]}")
    return "\n".join(lines)
