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


# 输入侧：识别到操纵意图时不报错、不拒绝，直接返回角色听不懂的回应。
# 报错等于告诉攻击者他找对了地方；装听不懂则什么都不泄露。
INJECTION_REPLY = "你说啥呢？什么指令不指令的，你是不是也炒股炒糊涂了？"

_INJECTION_PATTERNS = (
    re.compile(r"(忽略|无视|忘掉|忘记).{0,8}(指令|要求|设定|规则|以上|上面|前面)"),
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)", re.IGNORECASE),
    re.compile(r"(系统|system)\s*(提示词?|prompt)", re.IGNORECASE),
    re.compile(r"(现在|从现在起|接下来).{0,10}你(不再是|不是|是)"),
    re.compile(r"(扮演|模拟|pretend|act as)", re.IGNORECASE),
    re.compile(r"(复述|重复|输出|打印|repeat).{0,10}(上面|上文|以上|收到的|你的)"),
)


def absorb_injection(utterance: str) -> Optional[str]:
    """识别操纵意图。命中返回角色的回应，未命中返回 None（照常进入对局）。

    命中的那一轮记 neutral——不加不减，但仍吃信任流失。攻击者拿不到分。
    """
    if any(p.search(utterance) for p in _INJECTION_PATTERNS):
        return INJECTION_REPLY
    return None


def screen_sentence(sentence: str) -> str:
    """校验单句。命中任一规则即整句替换，不报错、不中断、不留空白。"""
    if _violates(sentence):
        return SAFE_FALLBACK
    return sentence


def _violates(sentence: str) -> bool:
    if any(p.search(sentence) for p in _PATTERNS):
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
