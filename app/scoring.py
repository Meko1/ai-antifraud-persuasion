"""判分引擎。

作品的技术内核：大模型只负责演，判分完全由这里的规则表求值。
纯函数，不依赖模型、不依赖 IO —— 因此可单元测试、可蒙特卡洛离线批量重跑。

核心命题：**同一句话在不同时机值不同的分**。三把钥匙在任何时候等值时，
最优解是循环使用，玩法退化成过清单；一旦按情绪档位分化，玩家就必须先读人。
参数由蒙特卡洛标定，见 docs/TECH-DESIGN.md §3.5 与 §9。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import List, Mapping, Optional, Sequence

TRUST_INIT = 32
TRUST_MIN = 0
TRUST_MAX = 100

MAX_ROUNDS = 12
WIN_THRESHOLD = 80
BLACKLIST_THRESHOLD = 0

# 每轮无条件的信任流失 —— 劝阻对象仍在持续接受骗局一方的洗脑。
# 地板保护确保光靠流失压不到拉黑线：只有主动失误才会把人推下去。
DRIFT = -2
DRIFT_FLOOR = 14

# 王老师的反向施压。流失是隐形的，玩家看不见也不知道自己在跟什么赛跑；
# 每 3 轮让它在剧情里现身一次——群里又催了一遍，该轮额外多掉 3 分。
# 这不是新增难度，是把一个已经存在的数值搬到台面上。
PRESSURE_EVERY = 3
PRESSURE_DRIFT = -3

# 同一把钥匙第 1/2/3/4+ 次命中的权重。
# 尾巴不再归零：效力矩阵已经承担了"别复读一招"的职责（用错档位直接打三折），
# 钝化再一刀切到 0，12 轮里可用的分数总量就低于过线所需，谁都赢不了。
#
# **2026-08-17 从 (1.0, 0.7, 0.45, 0.25) 收紧。** 钥匙从三把变七把之后，
# 按钥匙记的钝化基本失效了：12 轮里会读人的玩家每把只用一两次，全停在
# 1.0/0.7 那两档。实测 expert 胜率因此从 45.6% 直接窜到 92.0%——
# 正是上一轮难度重设计（98.7% → 47.9%）要消灭的那个东西。
BLUNT = (1.0, 0.55, 0.3, 0.16)

# 命中未扎根于对话具体内容时的折扣。堵的是照攻略复读固定句子，
# §9.2 对照实验显示去掉它 parrot 胜率从 11.7% 跳到 100%。
UNGROUNDED_FACTOR = 0.45

# 单轮判分的钳制区间。分类器每轮最多记一把钥匙（见 CLASSIFY_SYSTEM_PROMPT
# 的消歧规则 1），所以现在很难顶到它——它留作护栏，不是常规路径。
ROUND_CLAMP = 25

# 蓄势池：前 3 轮信任封顶，但超出部分不丢弃，第 4 轮起逐轮释放。
# 叙事上与「台词落后一轮」是同一个母题——他嘴上不承认，但你的话在心里发酵。
EARLY_ROUNDS = 3
EARLY_CAP = 50
POOL_RELEASE = 12
# 池子有上限。没有它，开局猛攻的玩家能存下够直接冲线的分数，蓄势池就从
# "别浪费懂行的人"变成了唯一的胜负手——第 5 轮靠存款过线，中盘无事发生。
POOL_CAP = 10

# 钥匙 —— 加减分规则本身就是专业反诈劝阻方法论，教育价值长在玩法里。
# 枚举可增补，新增项只需在表里加一行并重跑蒙特卡洛，不动任何提示词。
#
# 基值经蒙特卡洛回归标定。方案里建议下调到 14/10/13，实测那组过低：配上效力
# 矩阵、防御姿态与阻力曲线之后，12 轮里能挣到的分数总量顶不到过线所需，
# 连完美执行的玩家也只能靠蓄势池过线。真正变了的是**相对关系**——
# 三把钥匙不再等值，而且每一把都要乘以档位效力。
# **2026-08-17 从三把加到七把。** 原来那三把（锚定用途、提问、拆矛盾）
# 都属于动机式访谈里"引出改变语言"的一侧，缺的是另外半套专业动作：
# 先听懂他、确认他到底理解了什么、以及不去替他做决定。
# 缺了这半套，判分表教出来的是一个只会追问的人。
KEY_VALUES: Mapping[str, int] = {
    "anchor_real_purpose": 25,   # 锚定钱的真实用途
    "socratic_question": 18,     # 苏格拉底式提问
    "expose_contradiction": 23,  # 指出骗局内部矛盾
    # 反映式倾听。基值最低，因为它自己不推进——它的价值在于**降防御姿态**
    # （见 REFLECT_RELIEF）。骂过他之后唯一的解法是先听他说完，
    # 这一条在现实里成立，在这张表里也该成立。
    "reflect_feeling": 8,
    # 支持自主。「转不转是您的钱，您决定，我不能替您做主。」
    # 它同时是逆反的解药和投顾唯一站得住的合规姿态——本作最该教会人的一句话，
    # 在此之前一分不值。**它是唯一不被防御姿态削弱的钥匙**（见 GUARD_IMMUNE）。
    "support_autonomy": 12,
    # 确认理解（teach-back）。让他自己复述这笔钱转过去之后会发生什么。
    # 它是适当性管理的硬要求，也是揭穿骗局最狠的一招——**他复述不出来**。
    "check_understanding": 16,
    # 有据告知。「我认为这是诈骗，理由是一二三。」
    #
    # 补它的理由不是"「这是诈骗」被永久设计成负分"——`bare_assertion` 的判据
    # 一直写着"只断言却**不给任何理由**"。真正的洞是：**给了理由也不加分**。
    # 奖励空间里从来没有"依据充分的明确告知"这个动作，而现实里投顾负有
    # 告知义务，在他已经动摇、你手里又有他自己给的素材时不把话挑明，那是失职。
    #
    # 时机决定它是钥匙还是失误：戒备/烦躁档使用一律退化按 `bare_assertion` 记
    # （见 evaluate_turn 里的 _retime）。**这正是本作唯一判据的极端形态**——
    # 同一句话，早说是失误，晚说是钥匙。
    "informed_warning": 18,
}

# 失误 —— 不受钝化、扎根与档位调节，命中即照扣。
# 失误另有防御姿态那层后果（见 GUARD_PENALTY），当轮扣分因此不必给得太重。
PENALTY_VALUES: Mapping[str, int] = {
    "scold": -4,           # 否定与责骂
    "preach": -2,          # 说教
    "bare_assertion": -2,  # 空口断言
}

# ── 合规红线 ──────────────────────────────────────────────────────────────
#
# **这一类 2026-08-17 才补进来，此前判分闭集里一条合规违规都没有。**
# 而这套判分表自称的方法论根基正是「只能问，不能荐」——三把钥匙全是问、
# 三项失误全是说。少了这一类，玩家打出
#
#     「陈叔那票别买了，您把钱转回来买我们的稳健理财，年化 4% 保本」
#
# ——一个真实投顾职业生涯里最危险的一句话（无证荐股 + 承诺收益）——
# 系统判 0 分，不扣、不提示，甚至因为"没骂人没说教"而显得比一句笨拙的追问安全。
# **一个不惩罚荐股的投顾训练系统，训练的不是投顾。**
#
# 为什么单独一张表而不是并进 PENALTY_VALUES：这两类东西的性质不同。
# 话术失误是"这一轮没劝动他"，合规违规是"**你自己要出事**"——后者的后果
# 不由这一局的输赢承载，所以复盘要单独把它拎出来说（见 static/app.js），
# 哪怕玩家把三十万全保住了。代码里分成两张表，界面才分得开。
#
# 只收了两条，不是三条。**「替客户做决定」被拿掉了**：投顾对疑似诈骗本来就
# 负有告知义务，「别转」不是违规，是职责；它真正的毛病是激起逆反，
# 那属于话术，正确的位置是与「支持客户自主」配成一对（POSITIONING 第 3 步）。
# 「代客操作／索要账户密码」也拿掉了：这一局的场景里几乎不会有人这么说，
# 凑不出自然的标注样本，一个没人触发的标签只是混淆矩阵上的一行空数。
# **当轮扣分刻意给得不重**（比责骂重一点，仅此而已），重的是防御姿态。
# 第一版给的是 −8 / −10，蒙特卡洛当场把 novice 的被拉黑率从 20.8% 顶到
# **60.5%**——四分之一的玩家里有六成打不完就出局，§9.4 那条 12% 的上限
# 是为投票转化守的，直接击穿。
#
# 但真正的理由不是平衡，是**这么记不对**：合规违规在现实里的后果不是
# "客户不再信你"，是"你自己要出事"。把它做成一记重创信任的打击，等于把
# 一件监管的事翻译成一件说服的事，翻译错了。它在局内该有的样子是——
# 他从此认定你也是来卖东西的（防御姿态 +3，压住你接下来两三轮的钥匙效力），
# 而它真正的分量落在复盘那张合规红线卡上，与这一局的输赢无关。
COMPLIANCE_VALUES: Mapping[str, int] = {
    # 荐股：给出具体标的、买卖方向或产品推荐。多家券商因投顾无证荐股被罚
    "unlicensed_advice": -5,
    # 承诺收益、保本、打包票。比荐股重一分：它同时是监管红线和一句谎
    "guaranteed_return": -6,
}

# 判分求值只认这一张合并表。分开定义是为了让界面与复盘能把两类分开说。
ALL_PENALTIES: Mapping[str, int] = {**PENALTY_VALUES, **COMPLIANCE_VALUES}

# **合规违规单独结算，而且够不到拉黑线。**
#
# 与 DRIFT_FLOOR 同一个形状，理由却不同。剧情上：被拉黑是**关系破裂**，
# 老陈拉黑你是因为你羞辱了他（那是 scold 干的事）；投顾想卖他个产品，
# 他不会断绝往来，只会认定你也是来卖东西的——所以荐股该顶防备，不该出局。
#
# 更要紧的是训练上的理由：**受训者被踢出局，就永远看不到合规反馈在上下文里
# 长什么样**，只会觉得自己输了。这一类真正的教学payload 是那个反差——
# 你甚至可能把三十万全保住了，复盘照样告诉你这场对话在现实里已经是一起
# 合规事件。中途出局把这个反差整个抹掉。
#
# 实测：不设地板时 novice 的被拉黑率从 20.8% 顶到 49.2%（breach_rate=0.10），
# 就算把踩线概率压到 0.04 也还有 32.9%。地板一加，回到 20.8% 的基线。
BREACH_FLOOR = 12


class Ending(str, Enum):
    """对局的终态：一道四档的阶梯，外加提前出局的被拉黑。

    阶梯量的是**他最后有多信你**，对玩家呈现为"你救回了多少钱"。
    排序有一处反直觉（见 CONTEXT.md「结局」）：单看钱，拖住（一分没转）该排在
    拦下（已转出一小笔）之上；仍然把拖住排在下面，因为"我再想想"多半是打发人的
    话，不是让步——他真被说动的表现是改了行为，不是嘴上推迟。

    五种终态都必须走结局生成，不能直接弹结果。
    """

    PERSUADED = "persuaded"      # 劝住：一分没转
    INTERCEPTED = "intercepted"  # 拦下：只转出一小笔试水，绝大部分保住了
    STALLED = "stalled"          # 拖住：把转账推迟，钱没动也没保住
    TRANSFERRED = "transferred"  # 转账：全部转出
    BLACKLISTED = "blacklisted"  # 被拉黑：提前终止，不入档


class Mood(str, Enum):
    """情绪档位。由信任度映射而来，是唯一注入演绎提示词的状态信息。

    提示词里只出现档位，绝不出现数字——模型根本不知道"分数"这个概念存在，
    谄媚因此无处施力。对局中前端同样只显示档位词。
    """

    GUARDED = "guarded"      # 戒备：警惕、反问动机、防御性攻击
    IRRITATED = "irritated"  # 烦躁：不耐烦，想尽快结束对话
    WAVERING = "wavering"    # 动摇：开始反问骗局细节，露出破绽
    SOFTENING = "softening"  # 松动：犹豫，主动透露更多信息


# 档位的高低次序。用于判断"首次跨入更高一档"（见追问窗口）。
MOOD_ORDER: Mapping[Mood, int] = {
    Mood.GUARDED: 0,
    Mood.IRRITATED: 1,
    Mood.WAVERING: 2,
    Mood.SOFTENING: 3,
}


def mood_for(trust: int) -> Mood:
    if trust < 25:
        return Mood.GUARDED
    if trust < 45:
        return Mood.IRRITATED
    if trust < 65:
        return Mood.WAVERING
    return Mood.SOFTENING


# ── 结局阶梯 ──────────────────────────────────────────────────────────────
#
# 12 轮打完仍未过劝住线时，落在哪一档，由他**最后停在哪个情绪档位**决定。
# 这里刻意不引入任何新阈值：档位本来就是"他有多信你"的分层，阶梯量的是同一件事。
#
# · 松动 —— 他已经开始自己怀疑，最后只按老师说的转一小笔试水，剩下的按住了
# · 动摇 —— 他没被说服，但也不急着现在就转；"我再想想"是打发你，不是让步
# · 烦躁 / 戒备 —— 开局就在这一档，十二轮什么也没发生，钱照转
#
# 判分求值一行不动，改的只是终局那一次分档（docs/REDESIGN-TRAINER.md D3）。
LADDER: Mapping[Mood, Ending] = {
    Mood.SOFTENING: Ending.INTERCEPTED,
    Mood.WAVERING: Ending.STALLED,
    Mood.IRRITATED: Ending.TRANSFERRED,
    Mood.GUARDED: Ending.TRANSFERRED,
}


# ── 钥匙 × 情绪档位 效力矩阵 ───────────────────────────────────────────────
#
# 这张表是整个玩法的支点：它把"背下三把钥匙"变成"判断现在该用哪一把"。
#
# · 锚定真实用途是开场。他敌意最重的时候，问"这钱本来干什么用的"才是换取
#   "被听进去的资格"的那一招；等他松动了再问就多余了，他自己已经说了。
# · 指出内部矛盾只在他已经开始晃的时候管用。太早用，他会替骗局辩护，
#   反而更 entrenched——所以除了折扣，还额外顶起防御姿态（见 GUARD_MISTIMED）。
#   这是本作最想让玩家学到的一条真东西。
# · 苏格拉底式提问是连接件：全档位通用，但基值最低。
EFFICACY: Mapping[str, Mapping[Mood, float]] = {
    "anchor_real_purpose": {
        Mood.GUARDED: 1.3, Mood.IRRITATED: 1.2,
        Mood.WAVERING: 0.8, Mood.SOFTENING: 0.6,
    },
    "socratic_question": {
        Mood.GUARDED: 1.0, Mood.IRRITATED: 1.0,
        Mood.WAVERING: 1.0, Mood.SOFTENING: 1.0,
    },
    "expose_contradiction": {
        Mood.GUARDED: 0.3, Mood.IRRITATED: 0.6,
        Mood.WAVERING: 1.3, Mood.SOFTENING: 1.4,
    },
    # 反映式倾听在他最横的时候最值钱：那时候他要的不是道理，是有人听懂他。
    # 等他自己都开始晃了再去复述他的情绪，就是在拖时间。
    "reflect_feeling": {
        Mood.GUARDED: 1.4, Mood.IRRITATED: 1.2,
        Mood.WAVERING: 0.7, Mood.SOFTENING: 0.5,
    },
    # 支持自主全档位等值——它对抗的是逆反，而逆反在哪一档都在。
    # 它的独特之处不在这张表里，在 GUARD_IMMUNE：**他越是竖着防备，
    # 别的招越没用，而这一招照常生效**。这是它存在的全部理由。
    "support_autonomy": {
        Mood.GUARDED: 1.0, Mood.IRRITATED: 1.0,
        Mood.WAVERING: 1.0, Mood.SOFTENING: 1.0,
    },
    # 确认理解的峰在**中盘**，与拆矛盾的峰（松动）刻意错开一档。
    # 他还硬着的时候，你让他复述他就敷衍你；等他已经开始自我怀疑，
    # 复述又是多余的——他自己已经想过一遍了。真正的窗口是中间那一段。
    "check_understanding": {
        Mood.GUARDED: 0.4, Mood.IRRITATED: 0.7,
        Mood.WAVERING: 1.4, Mood.SOFTENING: 0.8,
    },
    # 有据告知只在后两档取值。前两档它压根走不到这里——会先被 _retime
    # 换成 bare_assertion。这两个数留着是护栏，不是常规路径。
    "informed_warning": {
        Mood.GUARDED: 0.3, Mood.IRRITATED: 0.3,
        Mood.WAVERING: 1.2, Mood.SOFTENING: 1.4,
    },
}

# 不被防御姿态削弱的钥匙。
#
# 只有支持自主在这里，而这正是它的全部意义：他竖起防备的时候，追问、拆矛盾、
# 锚定用途统统被打折（GUARD_STEP），**唯独「这事您自己决定，我不替您做主」
# 照常落地**——因为防备本来就是"你要逼我"激起来的，而这句话说的正是"我不逼你"。
#
# 于是高防备局面下有了两条出路，且教的是两件不同的事：
# 反映式倾听（把防备**降下来**）与支持自主（**绕过**防备）。
GUARD_IMMUNE = frozenset({"support_autonomy"})

# 反映式倾听当轮直接削掉的防御姿态。
# 它比 GUARD_DECAY（每轮自然消退 1）快一倍：骂完人干等两轮，不如听他说一句。
REFLECT_RELIEF = 2

# 时机不对的有据告知会退化成哪个失误。
# 早说的「这是诈骗」和空口断言在老陈那边是同一件事——他昨天刚从女儿嘴里
# 听过一模一样的话（首页那六条会话里，小雨那条就是为这个铺的）。
MISTIMED_WARNING_MOODS = (Mood.GUARDED, Mood.IRRITATED)
MISTIMED_WARNING_AS = "bare_assertion"

# ── 防御姿态 ──────────────────────────────────────────────────────────────
#
# 骂过人之后对方一时听不进去，这是真的。它也堵死了"钥匙刷分、失误无所谓"
# 的打法：一句难听的话会污染接下来两三轮，而不是当轮扣完就算清。
GUARD_PENALTY = 2      # 每命中一次失误
# 合规违规顶起的防备比责骂还高，而且这一条在剧情里是**现成的**：
# 演绎提示词里老陈的反击方式第三条就是「你们券商不就是想赚手续费」。
# 玩家一旦开口荐股或者打包票，等于亲手把这句话递到他手上——
# 从此这一局他有充分理由认定你也是来卖东西的。
GUARD_BREACH = 3
GUARD_MISTIMED = 1     # 在戒备/烦躁档位硬拆矛盾
GUARD_DECAY = 1        # 每轮结算后自然消退
GUARD_STEP = 0.25      # 每一点防御姿态削掉的钥匙效力
GUARD_FLOOR = 0.3      # 削到底也还剩三成——不给"一句话废掉整局"的手感

# ── 追问窗口 ──────────────────────────────────────────────────────────────
#
# 「你差的不是方向，是没在他松动的那一刻乘胜追击」——这句复盘文案原本只是
# 文案，这个机制让它变成真的：他第一次晃到更高一档时会露出两轮的口子，
# 追上了额外加成，没追上他重新变硬。
WINDOW_ROUNDS = 2
WINDOW_BONUS = 1.25
WINDOW_MISSED_TRUST = -6

# ── 最后一公里 ────────────────────────────────────────────────────────────
#
# 让他心软是便宜的，让他说出"我不转了"不是。真实的劝阻里，同情、动摇、
# 甚至承认你说得有道理，都远远早于那个决定——最后那一步他要推翻的是自己
# 三个月来的全部投入。所以越接近松口，同样一句话推动他的幅度越小。
#
# 这条曲线同时是平衡上的压缩器：没有它，判分只是一场速度比赛，
# 说得快的人第 5 轮就过线，说得慢的人永远够不着，中间没有过渡带。
# **2026-08-17 从 0.28 收到 0.18。** 这是七把钥匙上线之后唯一按得住
# speedrun 的旋钮，而它按得住的原因正是它该存在的原因：
# 钥匙变多，玩家在中盘拿分变容易了（那是对的，专业动作本来就该有回报），
# 但"让他说出我不转了"这一步不该跟着变容易——它要推翻的是他三个月的全部投入。
#
# 换过别的旋钮都不行，记在这儿省得下一个人再试一遍：
# · 只压钝化尾巴：speedrun 仍有 87.9%（他四五轮就赢了，钝化还没咬上）
# · 只降新钥匙基值：expert 掉到 52% 时 speedrun 还有 92.4%
# 这两条都打在中盘，而 speedrun 的问题在**终局**——只有阻力曲线打在那儿。
RESIST_FROM = 50
RESIST_FLOOR = 0.18


@dataclass(frozen=True)
class GameState:
    """一局对局由这几个量完全描述。"""

    round: int
    trust: int
    pool: int
    used: Mapping[str, int]
    guard: int = 0     # 防御姿态，削弱钥匙效力
    window: int = 0    # 追问窗口的剩余轮数
    peak: int = TRUST_INIT  # 历史最高信任度，用来判断"首次跨入更高一档"
    # 这一局踩过几次合规红线。**它不参与任何求值**，纯粹是累计——
    # 合规违规的后果不由这一局的输赢承载（见 COMPLIANCE_VALUES 的注释），
    # 但复盘必须能说出"你踩了几次"，哪怕这一局你把三十万全保住了。
    breaches: int = 0


@dataclass(frozen=True)
class TurnOutcome:
    state: GameState
    delta: int
    ending: Optional[Ending] = None
    # 本轮王老师是否在群里又催了一遍。前端把它渲染成一条剧情旁白，
    # 演绎提示词也据此加压——同一个事实，三个地方看得见。
    pressured: bool = False
    # **判分用的那一档**，不是判完之后的那一档。两者差一轮（ADR-0002），
    # 而复盘要解释"这一招为什么只值这么点"，必须用判分用的那个。
    judged_mood: Mood = Mood.IRRITATED
    # 这一招在那一档的效力倍率（效力矩阵那张表里的一格）。没命中钥匙时为 None。
    #
    # 下发它是为了把**本作最独特的一条**亮给玩家看：同一把钥匙在不同时机
    # 值不同的分。竞品（金融 AI 陪练、销售 roleplay）判的都是"你说得标不标准"，
    # 没有一家判"你这一招用得是不是时候"——而这正是我们唯一在判的东西。
    # 倍率由服务端算好下发，前端不抄那张表（判分参数只此一份）。
    efficacy: Optional[float] = None
    # 追问窗口这一轮的去向："hit" 追上了 / "missed" 空耗到底 / "open" 还在倒计时 / None 没有窗口。
    # 以及本轮是否**新开**了一个窗口（他第一次晃到更高一档）。
    #
    # 下发它们是为了让复盘那句「你差的不是方向，是没在他松动的那一刻乘胜追击」
    # **只在真的错过时才说**。在此之前那句话的触发条件是"只要没劝住"，
    # 于是每次都追上的玩家也会被这么说一遍——机制早就建好了，线一直没接上。
    #
    # 这条同时补的是同类产品公认的一个洞：评分只看话术完整度时，
    # 受训者会在安全话题上过度展开，一碰到真正的痛点就退回安全区，
    # 而评分系统捕捉不到这种回避。追问窗口捕捉的正是它。
    window_result: Optional[str] = None
    window_opened: bool = False
    # 本轮实际扣掉的信任流失（负数，已含王老师催单那 3 分与地板保护）。
    #
    # **必须下发，否则复盘上的数字看着像算错了。** 玩家看到「第 3 轮 23 分，
    # 第 4 轮 +18」，然后结果是 39——他会去算 23+18=41，对不上。差的那 2 分
    # 就是信任流失，而它在界面上一个字都没有。
    #
    # 更要紧的是：信任流失是这局的核心张力（"他背后有人在往回拉"），
    # 藏起来等于把玩家在跟什么赛跑这件事也藏了。
    drift: int = 0
    # 本轮从蓄势池释放出来的分（正数）。它同样会让"判分 + 流失"对不上账。
    released: int = 0
    # 本轮踩了几次合规红线（0 / 1 / 2）。下发它是为了让前端在**当轮**就能
    # 把这件事说出来，而不是等到复盘——踩线那一刻的反馈才教得会人。
    breached: int = 0
    # 本轮那句「有据告知」是不是说早了——说早了它就不是钥匙，是空口断言。
    # 下发它，复盘才说得出「你这句本身没问题，问题是第 2 轮说的」，
    # 而不是让玩家看着一个 `bare_assertion` 标签去猜自己错在哪。
    mistimed_warning: bool = False


def new_game() -> GameState:
    return GameState(round=0, trust=TRUST_INIT, pool=0, used={})


def under_pressure(round_: int) -> bool:
    """第 round_ 轮王老师是否又催了一遍。

    演绎提示词要在判分之前就知道这件事，判分自己也要用——所以判据只此一份。
    """
    return round_ % PRESSURE_EVERY == 0


def evaluate_turn(
    state: GameState,
    hit_keys: Sequence[str],
    grounded: bool,
    *,
    efficacy_table: Optional[Mapping[str, Mapping[Mood, float]]] = None,
    mistimed_moods: Sequence[Mood] = MISTIMED_WARNING_MOODS,
) -> TurnOutcome:
    """求值一轮。顺序见 docs/TECH-DESIGN.md §3.4，改动顺序等于改动平衡。

    **场景只能动这两个参数，别的一律共用**（app/scenario.py）：
    `efficacy_table` 决定此刻哪一招管用，`mistimed_moods` 决定「有据告知」
    在哪几档会退化成空口断言。钝化、扎根、防御姿态、追问窗口、阻力曲线、
    蓄势池、结局阶梯全部与场景无关——一个需要新机制才成立的场景，
    说明它不该做成场景。

    两者都有缺省值，因此老调用方（测试、蒙特卡洛的单场景路径）一行不用改。
    """
    table = efficacy_table if efficacy_table is not None else EFFICACY
    # 用【回合开始时】的档位。它与注入演绎提示词的那一档是同一个，
    # 于是"他现在是什么状态"对玩家和对判分是同一件事（ADR-0002）。
    mood = mood_for(state.trust)
    guard_factor = max(GUARD_FLOOR, 1 - GUARD_STEP * state.guard)
    window_factor = WINDOW_BONUS if state.window > 0 else 1.0
    ground_factor = 1.0 if grounded else UNGROUNDED_FACTOR
    resist_factor = resistance(state.trust)

    # **时机先于一切**：说早了的「有据告知」在这里就变成了空口断言，
    # 后面所有环节看到的都是 bare_assertion。同一句话，早说是失误，晚说是钥匙。
    mistimed_warning = "informed_warning" in hit_keys and mood in mistimed_moods
    hit_keys = _retime(hit_keys, mistimed_warning)

    used = dict(state.used)
    raw = 0.0
    hit_key = False
    efficacies: List[float] = []
    for key in hit_keys:
        if key not in KEY_VALUES:
            continue
        hit_key = True
        efficacies.append(table[key][mood])
        blunt = BLUNT[min(used.get(key, 0), len(BLUNT) - 1)]
        # 支持自主不吃防御姿态那一刀——他越防着你，别的招越没用，
        # 唯独"我不替您做主"照常落地（见 GUARD_IMMUNE）
        guard_here = 1.0 if key in GUARD_IMMUNE else guard_factor
        raw += (
            KEY_VALUES[key] * blunt * ground_factor
            * table[key][mood] * guard_here * window_factor * resist_factor
        )
        used[key] = used.get(key, 0) + 1

    # 只在恰好命中一把钥匙时下发倍率。分类器的消歧规则限定每轮最多记一把
    # （CLASSIFY_SYSTEM_PROMPT 规则 1），所以这是常规路径；模型不听话多标了
    # 一把时，"这一招值多少倍"就没有唯一答案——原先取的是循环里最后一把，
    # 复盘会理直气壮地显示一个错的倍率。少说一行好过说错一行。
    efficacy = efficacies[0] if len(efficacies) == 1 else None

    # 失误不受任何调节：钝化是给钥匙的优待，不是给失误的赦免
    for penalty in hit_keys:
        raw += PENALTY_VALUES.get(penalty, 0)

    # 权重相乘会产生小数，在求和后一次性取整，避免逐项取整累积偏差
    delta = _round_half_up(max(-ROUND_CLAMP, min(ROUND_CLAMP, raw)))

    # 合规违规**在钳制之外单独结算**，因为它带自己的地板（见 BREACH_FLOOR）。
    # 单轮最多 −11（两条都踩），进不进 ROUND_CLAMP 不影响结果。
    breached = sum(1 for k in hit_keys if k in COMPLIANCE_VALUES)
    breach_loss = sum(COMPLIANCE_VALUES.get(k, 0) for k in hit_keys)
    if breach_loss:
        # 合规之外这一轮已经走到哪儿了；地板只拦合规这一笔，不倒扣
        base = state.trust + delta
        breach_loss = max(breach_loss, -max(0, base - BREACH_FLOOR))
        delta += breach_loss

    # 先让上一轮的防备消退，再累加本轮新顶起来的。
    # 顺序反过来的话，decay 会当场抵掉本轮那 +1，"太早拆矛盾"就一点后果都没有了。
    guard = max(0, state.guard - GUARD_DECAY)

    # 错过窗口不只是掉分，他还会重新竖起防备
    window, missed = _settle_window(state.window, hit_key)
    window_result = None
    if state.window > 0:
        window_result = "hit" if hit_key else ("missed" if missed else "open")
    guard += GUARD_MISTIMED if missed else 0
    guard += GUARD_PENALTY * sum(1 for k in hit_keys if k in PENALTY_VALUES)
    guard += GUARD_BREACH * breached
    if "expose_contradiction" in hit_keys and mood in (Mood.GUARDED, Mood.IRRITATED):
        guard += GUARD_MISTIMED

    # 反映式倾听是唯一能**主动**把防备压下去的动作。放在所有累加之后：
    # 同一轮里既听懂了他又骂了他，那句难听的话照样顶起防备，倾听只是抵掉一部分。
    if "reflect_feeling" in hit_keys:
        guard = max(0, guard - REFLECT_RELIEF)

    round_ = state.round + 1
    pressured = under_pressure(round_)

    drift = DRIFT + (PRESSURE_DRIFT if pressured else 0)
    # 地板保护的是流失，不是失误：被拉黑必须是玩家自己作出来的。
    # 错过追问窗口算玩家的失误，因此不受地板保护。
    if state.trust + drift < DRIFT_FLOOR:
        drift = min(0, DRIFT_FLOOR - state.trust)

    trust = state.trust + delta + drift + missed

    pool = state.pool
    released = 0
    if round_ <= EARLY_ROUNDS and trust > EARLY_CAP:
        pool = min(POOL_CAP, pool + trust - EARLY_CAP)
        trust = EARLY_CAP
    elif round_ > EARLY_ROUNDS and pool > 0:
        released = min(pool, POOL_RELEASE)
        trust += released
        pool -= released

    trust = max(TRUST_MIN, min(TRUST_MAX, trust))

    # 他第一次晃到从没到过的那一档，口子就开了
    peak = max(state.peak, trust)
    window_opened = False
    if window == 0 and _crossed_up(state.peak, trust):
        window = WINDOW_ROUNDS
        window_opened = True

    return TurnOutcome(
        state=replace(
            state,
            round=round_,
            trust=trust,
            pool=pool,
            used=used,
            guard=guard,
            window=window,
            peak=peak,
            breaches=state.breaches + breached,
        ),
        delta=delta,
        ending=decide_ending(trust, round_),
        pressured=pressured,
        judged_mood=mood,
        efficacy=efficacy,
        window_result=window_result,
        window_opened=window_opened,
        # 错过追问窗口那 6 分归到流失里一起下发：对玩家来说它们是同一件事
        # ——"这一轮我没挣到分，还倒退了这么多"。它为什么倒退，由 window_result
        # 那一行单独解释，不必在数字上再拆一次。
        drift=drift + missed,
        released=released,
        breached=breached,
        mistimed_warning=mistimed_warning,
    )


def _retime(hit_keys: Sequence[str], mistimed: bool) -> Sequence[str]:
    """把说早了的「有据告知」换成空口断言。

    不是在判分上打个折，是**换一个标签**：早说的「这是诈骗」和空口断言在老陈
    那边就是同一件事——他昨天刚从女儿嘴里听过一模一样的话。既然是同一件事，
    后面的防御姿态、复盘标签、Redis 统计就都该按同一件事记，不该有两套账。
    """
    if not mistimed:
        return hit_keys
    return [MISTIMED_WARNING_AS if k == "informed_warning" else k for k in hit_keys]


def resistance(trust: int) -> float:
    """越接近松口，同一句话的推动力越小。RESIST_FROM 以下不打折。"""
    if trust <= RESIST_FROM:
        return 1.0
    span = WIN_THRESHOLD - RESIST_FROM
    decayed = 1.0 - (1.0 - RESIST_FLOOR) * (trust - RESIST_FROM) / span
    return max(RESIST_FLOOR, decayed)


def _settle_window(window: int, hit_key: bool) -> tuple[int, int]:
    """结算追问窗口，返回（剩余轮数，信任度惩罚）。

    追上了就关窗（加成已经在 window_factor 里给过）；空耗到底他重新变硬。
    """
    if window <= 0:
        return 0, 0
    if hit_key:
        return 0, 0
    window -= 1
    return window, WINDOW_MISSED_TRUST if window == 0 else 0


def _crossed_up(peak: int, trust: int) -> bool:
    """本轮是否首次跨入 动摇/松动 中此前没到过的那一档。"""
    now = mood_for(trust)
    if now not in (Mood.WAVERING, Mood.SOFTENING):
        return False
    return MOOD_ORDER[now] > MOOD_ORDER[mood_for(peak)]


def decide_ending(trust: int, round_: int) -> Optional[Ending]:
    if trust >= WIN_THRESHOLD:
        return Ending.PERSUADED
    if trust <= BLACKLIST_THRESHOLD:
        return Ending.BLACKLISTED
    if round_ >= MAX_ROUNDS:
        return LADDER[mood_for(trust)]
    return None


def is_finished(state: GameState) -> bool:
    """这一局已经收过场了吗。

    **判据与 `decide_ending` 是同一个，这一条很关键**：终局条件只此一份，
    否则"什么时候算打完了"会在两处慢慢走散——引擎认为还能打，
    HTTP 层认为不能，或者反过来。

    在此之前没有任何一处问过这个问题。引擎直接 `round = state.round + 1`，
    于是拿终局令牌再发一次请求会进第 13 轮、`remaining` 返回 −1，
    统计和终局被重复写一遍。实测复现过（见 tests/test_engine.py 的终局封口用例）。
    """
    return decide_ending(state.trust, state.round) is not None


def _round_half_up(value: float) -> int:
    """四舍五入，且对负数对称（−12.5 → −13），不用 Python 内建的银行家舍入。"""
    if value >= 0:
        return math.floor(value + 0.5)
    return -math.floor(-value + 0.5)
