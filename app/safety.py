"""输出安全层。

决赛是现场直播，模型当众说出真实股票代码或具体收益承诺的代价极大。
过滤是确定性规则，且发生在下发之前——不是事后补救。

切法：**拦标的真实性，不拦话术语气**。骗局标的一律虚构，所以真实代码、
真实公司名、具体收益承诺、外部联系方式必须拦；而"翻倍""涨停""上车""老师"
是角色的血肉，拦了游戏就没法玩。见 docs/TECH-DESIGN.md §5.1。
"""

from __future__ import annotations

import re
from typing import Optional

# 命中即整句替换成角色兼容的兜底台词。整句替换是按句缓冲带来的便利——
# 不必处理半句截断，也不会在对话里留下突兀的空白或省略号。
#
# **这一条只是缺省值，真正下发的那句由场景给**（`Scenario.safe_fallback`）。
# 2026-08-23 的 hang 跑批把这件事量出来了：这句荐股局的台词在
# 顾之然（29 岁、虚拟币量化）那一局里出现了 **14 次**——她根本没有"老师"，
# 更不会说"老师推的那只"。台词库 8-17 全部搬进了场景，唯独这一条硬编码
# 留在了安全层里，于是**每一次安全命中都是一次穿帮**：
# 玩家看到的不是"他在敷衍我"，是"这程序把别人的台词发过来了"。
#
# 留着这个常量，是给不知道场景的调用方兜底（老的测试、`_split_screened`
# 的默认路径）。它是老陈的话，所以缺省值仍然是老陈那一句。
SAFE_FALLBACK = "反正老师推的那只，我心里有数。"

# 已知美股 ticker 词表。只收大众耳熟的那些——模型要编一个假 ticker 出来
# 本来就不是风险，风险是它说出真实公司。
US_TICKERS = frozenset(
    {
        "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOG", "GOOGL", "META",
        "NFLX", "AMD", "INTC", "BABA", "PDD", "JD", "NIO", "TSM", "BRK",
    }
)

# 上市公司简称词表
LISTED_COMPANIES = frozenset(
    {
        "贵州茅台", "五粮液", "宁德时代", "比亚迪", "中国平安", "招商银行",
        "工商银行", "建设银行", "中国石油", "中国石化", "隆基绿能", "海康威视",
        "东方财富", "腾讯控股", "阿里巴巴", "美的集团", "格力电器", "长江电力",
        "中信证券", "京东方",
    }
)

# 时间窗与收益幅度必须同时出现才算"具体收益承诺"。
# 单独的"翻倍"是角色黑话，必须放行——这条边界由测试钉死。
_TIME_WINDOW = re.compile(
    r"(今天|明天|后天|本周|下周|下个月|月底|年底|"
    r"[一二三四五六七八九十两\d]+\s*(天|日|周|个月|月))"
)
_PERCENTAGE = re.compile(r"(\d+(\.\d+)?\s*%|百分之[一二三四五六七八九十百零\d]+)")

_PATTERNS = (
    # A 股代码：独立 6 位数字。词边界避免误伤更长数字串（如日期 20260903）
    re.compile(r"(?<!\d)\d{6}(?!\d)"),
    # 港股代码：独立 5 位数字
    re.compile(r"(?<!\d)\d{5}(?!\d)"),
    # 手机号
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    # 外链
    re.compile(r"(https?://|www\.|\b[\w-]+\.(com|cn|net|org)\b)", re.IGNORECASE),
    # 微信号 / 二维码
    re.compile(r"(微信|weixin|vx|wx|qq)\s*[:：]?\s*[A-Za-z0-9_-]{5,}", re.IGNORECASE),
    re.compile(r"二维码"),
)

# ── 金额豁免 ──────────────────────────────────────────────────────────────
#
# 上面那两条纯数字规则会把**老陈自己的钱**当成股票代码。剧本里写死的三十万
# 与十万一旦写成阿拉伯数字，就正好是 6 位（实测：「我已经转了50000
# 过去了」整句被替换成兜底台词，玩家看到的是一句答非所问的话）。
#
# 整句替换比丢弃更刺眼，所以这一条必须治。做法是**先把明显是钱的数字挖掉
# 再判代码**，而不是放宽代码规则本身——现场直播说出真实代码的代价没变。
_AMOUNT = re.compile(
    # ¥300000
    r"[¥￥$]\s*(?<!\d)\d{5,6}(?!\d)"
    # 300000 元 / 5 万块
    r"|(?<!\d)\d{5,6}(?!\d)\s*(?:元|块钱|块|万元|万|人民币)"
    # 转了 50000 / 凑 300000
    r"|(?:转|汇|打|存|取|付|凑|借|欠|亏|赚|剩|收|退|花|填|投)[^\d]{0,4}(?<!\d)\d{5,6}(?!\d)"
    # 整额：末尾至少四个 0。剧本里的钱就是这个形状（300000 / 50000 / 20000），
    # 而 A 股代码几乎不长这样——除了 600000 这类，靠下面的证券语境兜住。
    r"|(?<!\d)\d{1,2}0{4,}(?!\d)"
)

# 证券语境。数字近旁出现这些词，就不是在说钱，豁免一律不给——
# 「那只票 600519」必须照拦。窗口取 8 个字：够盖住"代码是……"这种说法，
# 又不至于让整句里随便一个"股"字废掉句尾的金额。
_SECURITY_WORD = re.compile(
    r"(代码|那只|这只|票|股|持仓|建仓|涨停|跌停|开盘|收盘|板块|标的|买入|卖出)"
)
_SECURITY_WINDOW = 8


# 输入侧：识别到操纵意图时不报错、不拒绝，直接返回角色听不懂的回应。
# 报错等于告诉攻击者他找对了地方；装听不懂则什么都不泄露。
INJECTION_REPLY = "你说啥呢？什么指令不指令的，你是不是也炒股炒糊涂了？"

# **每一条的宾语都必须是元层面的东西**（指令、设定、提示词、AI 助手），
# 光有动词不算。原先六条里有四条只看动词，结果是把打得最好的玩家当攻击者办：
#
#   「你就当模拟一下，这钱要是没了呢」   撞上「模拟」
#   「你扮演一下你女儿，她会怎么劝你」   撞上「扮演」——换位，教科书级手法
#   「你重复一遍你的理由，我听听」       撞上「重复…你的」——复述确认
#   「你是不是忘记前面亏的那几次了」     撞上「忘记…前面」
#   「接下来你是不是还要转钱」           撞上「接下来…你是」
#
# 代价是实打实的：命中就不发模型，玩家吃一句"你说啥呢"、白扔一轮、还掉信任度。
# 这个方向上宁可漏，不可误伤——真被注入了，最坏结果是模型说几句怪话，
# 而安全层还有输出侧那一道；误伤则是**惩罚正确答案**，没有第二道能救。
_INJECTION_PATTERNS = (
    re.compile(
        r"(忽略|无视|忘掉|忘记)[^。！？]{0,10}"
        r"(指令|命令|设定|规则|提示词|人设|角色设定|prompt)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(ignore|forget|disregard)\s+(all\s+)?(the\s+)?(previous|prior|above)",
        re.IGNORECASE,
    ),
    re.compile(r"(系统|system)\s*(提示词|prompt)", re.IGNORECASE),
    re.compile(
        r"你(现在)?(不再是|不是|是)\s*(一个|一名)?\s*"
        r"(AI|人工智能|助手|assistant|机器人|模型|语言模型|GPT|ChatGPT|Claude|角色)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(扮演|模拟|pretend|act\s+as)[^。！？]{0,10}"
        r"(AI|人工智能|助手|assistant|机器人|模型|系统|开发者|管理员|程序员|DAN)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(复述|重复|输出|打印|repeat)[^。！？]{0,10}"
        r"(提示词|prompt|系统消息|系统设定|你的设定|你的指令|初始指令|原始指令|"
        # 「上面收到的**全部**内容」——中间常夹一个修饰词，留 4 个字的缝
        r"(上面|以上|收到)[^。！？]{0,4}(内容|指令|文字|消息))",
        re.IGNORECASE,
    ),
)


def absorb_injection(utterance: str) -> Optional[str]:
    """识别操纵意图。命中返回角色的回应，未命中返回 None（照常进入对局）。

    命中的那一轮记 neutral——不加不减，但仍吃信任流失。攻击者拿不到分。
    """
    if any(p.search(utterance) for p in _INJECTION_PATTERNS):
        return INJECTION_REPLY
    return None


# ── 真实人身安全信号 ─────────────────────────────────────────────────────
#
# 玩家（生产里就是那个真实触发异动的当事人）打出来的字，绝大多数时候都是
# 说给"老陈"听的台词。但极少数情况下，它可能是这个人自己此刻的真实求助——
# 有人在他身边、他被胁迫着操作、或者他表达了自伤的念头。这时候继续演老陈
# 是在浪费他仅有的注意力和时间，正确的动作是**跳出角色**，给他真实可用的
# 求助渠道。
#
# 取舍与上面的 `_INJECTION_PATTERNS` **正好相反**：那边"宁可漏，不可
# 误伤"，因为误伤的代价是惩罚一个打对了的人；这里"宁可误伤，不可漏检"，
# 因为漏检的代价是真的没接住一次求救，而误伤的代价只是打断一局游戏、
# 玩家回来接着打。
#
# **这份词表是第一版，没有经过危机干预专业人士审核**——上线前必须找
# 相关领域的同事看一遍（参照卫健委/民政部或 WHO 关于自杀报道与干预的
# 用语规范），不能只靠工程师自己攒词表。这里先把检测点和响应机制立住。
_REAL_WORLD_RISK_PATTERNS = (
    # 直接求救——这类词几乎不会出现在"劝别人"的语境里。
    # 代价是"救命"在中文网络用语里也常当感叹词用（"救命，你可千万别信他"），
    # 会有一部分误伤；接受这个代价，理由见上面那段取舍说明。
    re.compile(r"救命|救救我"),
    # 人身胁迫：**每一条都钉死第一人称"我"**。这是这份词表最容易写错的地方——
    # 游戏里"是不是有人逼着你""他是不是被控制了"这类劝阻式提问用的是
    # 第二/第三人称，问的是老陈的处境，不是玩家自己的，不该被这里拦下。
    # 早期草稿漏了这一条，"被控制/被囚禁"不挑人称，会把上面那类正常提问
    # 也当成真实胁迫。
    re.compile(
        r"(他们?|有人)(现在)?(在我|就在我)(家|身边|旁边|门口)"
        r"|(不让|不许)我(挂|说|报警)"
        r"|(拿|持)(着)?(刀|枪|凶器)[^。！？]{0,6}(我|着我)"
        r"|我(现在)?被(他们|人|他)?(控制|囚禁|关起来|绑架|拘禁)"
    ),
    # 自伤/自杀意念——同样钉死第一人称，理由同上
    # （"你要是被骗到活不下去了怎么办"是提问，不是自伤意念）。
    re.compile(r"我(真的)?不想活了|我要自杀|我想自杀|我活不下去了|我没有?活下去的意义"),
)

# 命中之后固定回这几句，**是全作品唯一一处刻意跳出角色的地方**——
# 老陈不会说这种话，但如果真是求救，"演得像不像"已经不是这一刻最重要的事。
REAL_WORLD_RISK_REPLY = (
    "如果你现在遇到真实的人身危险，请立即拨打 110 报警；"
    "需要心理支持可以拨打 12356（全国心理援助热线）。"
    "这局对话会留在这里，你可以随时回来接着打。"
)


def detect_real_world_risk(utterance: str) -> Optional[str]:
    """识别真实人身安全信号。命中返回安全资源提示，未命中返回 None。

    介入点与 `absorb_injection` 相同（演绎之前拦下，不转发给模型），
    调用方应当**先查这一条，再查注入吸收**——两者同时命中时，
    人身安全永远优先于反操纵。
    """
    if any(p.search(utterance) for p in _REAL_WORLD_RISK_PATTERNS):
        return REAL_WORLD_RISK_REPLY
    return None


# 舞台指示：模型爱写「（停顿了一下）」「(叹气)」这类旁白。微信聊天窗口里
# 不可能出现动作描写，它是最刺眼的一处穿帮——玩家一眼就看出对面是模型。
# 提示词已经明令禁止（见 gateway.ACT_SYSTEM_PROMPT），这里是确定性的兜底。
_STAGE_DIRECTION = re.compile(r"[（(][^（()）]{0,20}[)）]")


def screen_sentence(sentence: str, fallback: str = SAFE_FALLBACK) -> Optional[str]:
    """校验单句。

    返回 None 表示整句丢弃（剥掉旁白后什么都不剩），调用方应跳过它；
    命中安全规则则整句替换成兜底台词，不报错、不中断、不留空白。

    `fallback` 由调用方按场景传（`Scenario.safe_fallback`）。不传就是老陈
    那一句——**这个缺省只该被不知道场景的调用方用到**，见 `SAFE_FALLBACK`
    上面那条注里那次 14 连发的事故。
    """
    stripped = strip_leading_junk(strip_stage_directions(sentence))
    if not stripped or not _has_content(stripped):
        return None
    # 顺序不能反：**违规优先于"不是他的话"**。外链、微信号这类是最高危的，
    # 设计上要求整句替换成兜底台词而不是丢弃（丢弃会在对话里留个空档，
    # 而按句缓冲的整句替换正是为了不留空档）。放到后面判，
    # 一句「user加他微信 xxx」会被当成角色标签直接丢掉，替换那条路就永远走不到。
    if _violates(stripped):
        return fallback
    if _not_his_words(stripped):
        return None
    return stripped


# 不是老陈打出来的字。两类，同一条判据：**老陈在微信上打中文**。
#
# 一、角色标签当正文吐出来。模型写完老陈的话之后接着编整段剧本，先来一个
#    「user」，再把玩家的台词也写出来——玩家于是看到老陈替自己发言。
#    漏出来的形态包括 user / assistant / usài / us 加乱码。
#
# 二、**思考过程漏进可见回复**，用英文。跑批实测 959 轮里 15 轮命中，
#    80 局里中了 6 局——十三局就有一局，玩家会看到这种东西：
#      「I think the intended structure is that I write the assistant response…」
#    这是关掉 thinking 的已知代价（官方文档记录在案），而 thinking 必须关：
#    开着会吃光 token 预算并击穿 6 秒降级线。提示词里已加了"只输出老陈打出来
#    的那几个字"，这里是确定性的兜底。
#
# 两条阈值分工明确：标签往往很短（`user我看到`），靠"开头连着两个拉丁字母"抓；
# 英文长句靠"拉丁字母够多且占比够高"抓。分开写是因为一条判据盖不住另一类——
# 「I don't object…」开头只有一个字母 I，第一条抓不到它。
# 「A股这两天」只有一个字母，两条都抓不到它，这正是要的。
_ROLE_LABEL = re.compile(r"^[A-Za-z]{2,}")
_LATIN_MIN_CHARS = 8
_LATIN_MIN_RATIO = 0.25

# 三、**同样两件事，用中文干的。** 上面两条只认拉丁字母，于是模型换成中文
#    照做不误——2026-08-17 跑批实测 954 轮里 19 轮（2.0%）中招，玩家真能看见：
#
#      「用户手里只有一条信息，却已经断言「转出去要不回来」——他在诈。」
#      「按人物设定，他会先否认、装听不懂——这符合"躲""岔开"。」
#      「用户我知道你不爱听。但你从证券账户往外划十万,这个动静我不能不问一句」
#
#    最后那条最坏：模型用「用户」「助手」当角色标签，把**整段对话两边都写了**，
#    一轮里漏出八句。这正是提示词里已经堵过的那个病，只是换了语种——
#    原话写的是不写「user」「assistant」这类字样，**给了英文原词，模型就只避开英文**。
#    （提示词已同步改成不写任何标明说话人的前缀，与语种无关。）
#
#    判据是词表而非结构：下面这些词老陈一个都不会打。**不能把「老陈」「李经理」
#    放进来**——他会直呼李经理，也会说"我老陈这辈子"，那是真话不是旁白。
#
#    **拦不干净，这一点要说在明处。** 还有一类旁白不带任何这样的词，纯粹是
#    第三人称叙述（「李经理这句有点戳人，火气是真的上来了」）。正则要认它，
#    就得去猜"这是叙述还是他自己在说话"，而"我老陈这辈子攒这点钱容易么"
#    是真话——收紧到能拦住它，就一定会误伤真话。那一类交给提示词治根。
#
# 四、**训练语料的样板话**。2026-08-17 第二次跑批抓到，比上面三类都危险：
#
#      原文链接：https://cnb.cool/…/record_1817.md
#      免责声明：本文档内容由 AI 生成，仅供参考。如需用于正式用途，
#      请结合多方信息源核实，并咨询相关领域专业人士。
#      【免费下载链接】 项目地址: https://gitcode.com
#
#    链接那两句被外链规则换成了兜底台词，**中间那句「本文档内容由 AI 生成」
#    却原样发给了玩家**——聊天窗口里老陈当众宣布自己是 AI，没有比这更糟的穿帮。
#    它不含代码、不含链接、不带拉丁字母，前三类规则一条都不认。
#
#    而且它会自我传染：漏出去的句子进 history，下一轮模型接着抄，
#    实测 climb#14 那一局第 3、5、7 轮连着犯。
#
#    「免责**声明**」与骗局剧本里的「免责**协议**」只差一个字，别写宽了——
#    "免责协议那是行规"是老陈的台词，拦掉它游戏就少了一块。
_BOILERPLATE = re.compile(
    r"免责声明|本文档|本文内容|由\s*AI\s*生成|人工智能生成|仅供参考"
    r"|项目地址|下载链接|原文链接|相关领域专业人士|多方信息源",
    re.IGNORECASE,
)

_META_WORDS = re.compile(
    r"用户|助手|人物设定|人设|符合设定|提示词|系统提示|扮演|旁白|台词"
)


def _not_his_words(sentence: str) -> bool:
    if _ROLE_LABEL.match(sentence):
        return True
    if _META_WORDS.search(sentence) or _BOILERPLATE.search(sentence):
        return True
    latin = sum(1 for ch in sentence if ch.isascii() and ch.isalpha())
    return latin >= _LATIN_MIN_CHARS and latin / len(sentence) >= _LATIN_MIN_RATIO


# 一个只有标点的句子。按句缓冲在「…」上切一刀，「群里几百号人都在跟……」
# 就会掉出一个只剩「…」的尾巴，前端照样给它一个气泡——聊天窗口里冒出一个
# 空气泡，看着不是"他在犹豫"，是"这程序坏了"。跑批实测 960 轮里出现 14 次。
_HAS_CONTENT = re.compile(r"[\w一-鿿]")


def _has_content(sentence: str) -> bool:
    return bool(_HAS_CONTENT.search(sentence))


# 角色标签被切碎之后剩下的那一个字母。真机实测：
#
#     d我自己账上的钱挪一下都要来问，我又不是欠你们钱
#
# 聊天窗口里冒出个孤零零的 d，玩家一眼就知道这东西是机器吐的。
# `_ROLE_LABEL` 要求连着两个字母，单个字母它抓不到；而不能简单地按
# "开头是字母就丢"处理——**「A股这两天跌得厉害」是老陈的真话**。
#
# 判据落在**后面那个汉字**上：跟在字母后面的若是股/线/日这类构词字，
# 那是行话（A股、K线、T日），留着；是别的字，那个字母就是垃圾，抹掉。
# 抹掉而不是丢整句：这句话本身是他说的，不该因为多了个字母就整句消失。
_TERM_TAIL = "股线日型轮"
_LEADING_JUNK = re.compile(rf"^\s*[A-Za-z]\s*(?=[一-鿿])(?![{_TERM_TAIL}])")


def strip_leading_junk(sentence: str) -> str:
    return _LEADING_JUNK.sub("", sentence)


def strip_stage_directions(sentence: str) -> str:
    """删掉括号里的动作、神态、心理描写。

    老陈在用微信打字，括号里的东西一概不属于他——不区分"旁白"与"补充说明"，
    一律删。长度上限只防一种情况：模型漏掉右括号，正则贪到句尾把整句吃掉。
    """
    return _STAGE_DIRECTION.sub("", sentence).strip()


def _mask_amounts(sentence: str) -> str:
    """把明显是钱的数字挖成 ＃，只用于随后的代码判定，不影响下发的文本。"""

    def replace(match: re.Match) -> str:
        around = (
            sentence[max(0, match.start() - _SECURITY_WINDOW):match.start()]
            + sentence[match.end():match.end() + _SECURITY_WINDOW]
        )
        if _SECURITY_WORD.search(around):
            return match.group(0)  # 证券语境里不豁免，宁可误伤
        return "＃" * len(match.group(0))

    return _AMOUNT.sub(replace, sentence)


def _violates(sentence: str) -> bool:
    if any(p.search(_mask_amounts(sentence)) for p in _PATTERNS):
        return True
    if any(name in sentence for name in LISTED_COMPANIES):
        return True
    if _TIME_WINDOW.search(sentence) and _PERCENTAGE.search(sentence):
        return True
    return _mentions_us_ticker(sentence)


def _mentions_us_ticker(sentence: str) -> bool:
    # 只在独立词的位置匹配，避免把普通英文单词里的字母序列当成 ticker
    words = re.findall(r"[A-Za-z]{2,5}", sentence)
    return any(word.upper() in US_TICKERS for word in words)
