"""场景的形状：一个骗局场景由哪些字段构成。

**这里只有类型，没有任何一个场景的数据。** 六个场景各自一个文件，
在 `app/scenarios/` 下；把它们串成注册表的是 `app/scenario.py`。

拆开的理由是 2026-09-05 的评审意见：原来的 `scenario.py` 是 2041 行，
类型定义、六份剧本、注册表挤在一处——**改 `Scenario` 加一个字段要在
两千行里翻六遍**，而六份剧本之间没有任何依赖，本来就不该互相挡路。

拆的是文件，不是结构：字段一个没改，`payload` / `reveal` / `clues_surfaced`
一个字没动，`from .scenario import ...` 的写法全部照旧。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from ..persona import Persona
from ..scoring import EFFICACY, Ending, Mood


@dataclass(frozen=True)
class FactRow:
    """客户档案上的一行。带 warn 的那几行是玩家手上的牌。"""

    label: str
    value: str
    note: str = ""
    warn: bool = False


@dataclass(frozen=True)
class PhoneRow:
    """复盘揭晓里他手机上的一条会话。

    `test` 是正则**源码字符串**，随场景下发到前端，由 JS 侧 `new RegExp` 编译。
    判据是"**他**说没说过"——匹配跑在劝阻对象的台词上，与扎根同源。
    """

    avatar: str       # CSS 类名（av-group / av-bank / …）
    initial: str      # 头像上的字，群头像为空
    name: str
    time: str
    line: str
    clue: str
    test: str = ""    # 空 = 这一条本来就在投顾手里，不计入分母
    own: bool = False


@dataclass(frozen=True)
class Scenario:
    """一个骗局场景。"""

    id: str
    name: str            # 复盘与工作台上显示
    kind: str            # 一句话说清它靠什么驱动：贪 / 怕
    script: str          # 注入演绎提示词的骨架（原 SCAM_SCRIPT）
    speaker: str         # 提示词里怎么称呼他（"老陈" / "周淑琴"）
    # 「投顾找上来了，他看得见什么、看不见什么、他会拿什么挡你」。
    # **这一段必须随场景走**：老陈挡你的是沉没成本和从众，周阿姨挡你的是
    # 「办案纪律不许我说」——同一段通用文案套两个场景，第二个当场穿帮。
    context: str
    personas: Tuple[Persona, ...]

    # ── 演绎指示 ──────────────────────────────────────────────────────
    first_turn: str
    moods: Mapping[Mood, str]
    pressure: str
    endings: Mapping[Ending, str]

    # ── 兜底台词（绕过安全层直发，必须自身安全）────────────────────────
    lines: Mapping[Mood, Sequence[str]]
    ending_lines: Mapping[Ending, Sequence[str]]
    # 安全层命中之后整句替出的那一句。
    #
    # **它必须随场景走，和上面那三样是同一条理由。** 2026-08-23 的 hang 跑批
    # 量到：安全层原本硬编码的那句「反正老师推的那只，我心里有数」在顾之然
    # （29 岁、虚拟币量化）那一局里出现了 14 次——她没有"老师"，也不炒股票。
    # 每一次安全命中都变成一次穿帮。
    #
    # 三条写法要求，和 `lines` 完全一致：
    #   · **绕过安全层直发**，所以它自身必须过得了安全层（有测试守）
    #   · 不出现真实标的、公司名、百分比+时间窗、联系方式
    #   · 是这个人会说的话——它出现的时机是"他不想跟你细说"，写成回避语气
    safe_fallback: str
    # 施压那一轮界面上那句旁白（每 3 轮一次，多掉 3 分）。
    #
    # **和 safe_fallback 是同一个 bug 的第二处**：前端原本写死「王老师又在群里
    # 催了一遍」，三个地方（对话旁白、复盘逐轮、流失那一格的 title）。
    # 顾之然没有王老师，月娥姐的催单来自群主，周淑琴那边是"办案的"在电话里催。
    # 玩家在聊天窗口里看到一个本局根本不存在的人名，这比台词平庸严重得多。
    #
    # 写法：**一句话说清是谁在催**，不带分值（分值由前端拼，判分参数不下前端）。
    pressure_note: str

    # ── 界面素材 ──────────────────────────────────────────────────────
    peer: str            # 聊天页标题
    # 头像上那个字，以及界面上指代他/她的那个代词。
    # **代词不是细节**：状态条上写着「他现在 烦躁」，而这一局的客户是位阿姨，
    # 玩家一眼就看出这套界面是照另一个人做的。
    initial: str
    pronoun: str
    ping: str            # 投顾发出去的第一条
    client_name: str
    client_sub: str
    client_tag: str
    incident_title: str
    incident_lead: str
    incident_hint: str
    facts: Tuple[FactRow, ...]
    desk_note: Tuple[str, ...]   # 工作台底部那段交底，逐行
    phone: Tuple[PhoneRow, ...]
    total: int
    test_transfer: int
    payee: str
    ending_copy: Mapping[str, Mapping[str, str]]

    # 开场屏「本次异常金额」那一格。**缺省 0 = 用 `total`**，多数场景走缺省。
    #
    # 老陈是唯一的例外，而这个字段就是为他加的：他的 `total` 是三十万，
    # 但**券商侧只看得见十万**（今日转出 10 万 + 余额 2.8 万），另外二十万
    # 一直在他自己的银行卡上——PROJECT-INTRODUCTION §3 写着"投顾看不见"。
    # 把 `total` 印在开场屏上，等于开局就告诉玩家"总共三十万"，
    # 而三十万这个数是这一局要问出来的东西（老伴那条揭晓的判据里就有它）。
    #
    # **`total` 仍然照发**：复盘那几个金额（保住多少 / 试水之后剩多少）全靠它，
    # 而那些数是打完之后才画的。这一格只管开场屏上印哪个数。
    incident_money: int = 0

    # ── 判分：场景唯一能动的两处 ──────────────────────────────────────
    #
    # 缺省沿用全局效力矩阵。只覆写要翻过来的那几把，其余共用——
    # 抄一整张表过来，改判分参数时两张表必然走散。
    efficacy_override: Mapping[str, Mapping[Mood, float]] = field(
        default_factory=dict
    )
    # 「有据告知」在哪几档会退化成空口断言。
    # 老陈是后两档才说得；周阿姨相反，**越早说越好**，所以这里给空元组。
    mistimed_warning_moods: Tuple[Mood, ...] = (Mood.GUARDED, Mood.IRRITATED)

    # 这一局里，谁已经对这位客户说过「这是诈骗」，什么时候说的。
    # 复盘那句「跟{谁}说的那四个字落在同一个地方」直接印它，**空串就整句不印**。
    #
    # **这个字段 2026-08-30 才加，加它是为了修一处编造。** 在此之前
    # `review.js` 写死着「跟他女儿昨天说的那四个字」——那是照老陈写的，而
    # `mistimed` 这一支在四个场景上都可达：刘卫东的女儿是三天前说的（不是昨天）、
    # 林月娥**没有女儿**（警告过她的是老公）、顾之然的手机里**根本没有家人**。
    # 一半的可达场景里，复盘对玩家断言了一件那一局剧本里没发生过的事。
    #
    # 顾之然给空串不是偷懒：她那一局的设定就是没有人拦过她（`phone` 里
    # 五条没有一条来自家人），**这正是她最难劝的地方**，不该被一句套话抹平。
    warned_by: str = ""

    @property
    def efficacy(self) -> Mapping[str, Mapping[Mood, float]]:
        """本场景实际生效的效力矩阵。"""
        if not self.efficacy_override:
            return EFFICACY
        merged = {k: dict(v) for k, v in EFFICACY.items()}
        for key, row in self.efficacy_override.items():
            merged[key].update(row)
        return merged

    def payload(self) -> Dict[str, Any]:
        """下发给前端的那一份。**开局时下发的那一份，不含隐藏线索。**

        **前端不再写死任何剧本常量。** 金额、收款方、客户档案、揭晓清单
        原先散在 `app.js` 与 `index.html` 里，加第二个场景时那些地方
        没有一处会提醒你漏改了。

        ## `phone` 为什么搬走了（P1-14）

        揭晓清单（他手机上那几条：荐股群、银行短信、家人、反诈中心）
        **是这一局要挖的东西本身**，而开局响应把它连同 `clue`（"这一条说明了
        什么"）一起发给了浏览器。任何人打开开发者工具就能提前看到全部答案。

        比赛里这是公平问题；接进真实业务之后这是**实验数据可信度**问题——
        一批"看过答案的对局"会把干预效果算高，而没有任何字段能把它们标出来。

        所以它挪进了 `reveal()`，只在结局那一屏随 `ending` 事件下发。
        对正常玩家没有任何差别：他本来也是打完才看见的。
        """
        return {
            "id": self.id,
            "name": self.name,
            "peer": self.peer,
            "initial": self.initial,
            "pronoun": self.pronoun,
            "ping": self.ping,
            # 施压旁白。**不下发分值**——判分参数不进前端（踩过的坑 7），
            # "多掉 3 分"那半句由前端自己拼
            "pressure_note": self.pressure_note,
            # 复盘「同一句话，换个时候说」那一支要印的那半句。空串＝不印。
            # 它不是线索：玩家在对局中问出来的是**骗局**那一侧的东西，
            # 而"家里人劝过没有"这件事复盘之前对判分没有任何影响。
            "warned_by": self.warned_by,
            "client": {
                "name": self.client_name,
                "sub": self.client_sub,
                "tag": self.client_tag,
                "facts": [
                    {"label": f.label, "value": f.value,
                     "note": f.note, "warn": f.warn}
                    for f in self.facts
                ],
            },
            "incident": {
                "title": self.incident_title,
                "lead": self.incident_lead,
                "hint": self.incident_hint,
                # 开场屏印的那个数。**不是 `total`**，理由见字段定义处。
                "money": self.incident_money or self.total,
            },
            "note": list(self.desk_note),
            # **`phone` 不在这里**，见本方法的文档字符串。它在 `reveal()`。
            "money": {
                # **只给总额。** 它印在开场屏上（"本次异常金额"），是这一局的
                # 已知条件。收款方和试水金额不给——见 `reveal`。
                "total": self.total,
            },
            "endings": {k: dict(v) for k, v in self.ending_copy.items()},
        }

    @property
    def diggable(self) -> Tuple[PhoneRow, ...]:
        """要靠问才拿得到的那几条。`test` 为空的（开局就在投顾手里的那条银行
        短信，`own=True`）不算——它本来就不用挖，复盘的分母也是这么算的。
        """
        return tuple(r for r in self.phone if r.test)

    def clues_surfaced(self, texts: Iterable[str]) -> int:
        """这一局劝阻对象把几条线索抖了出来。**纯函数，可离线重跑。**

        `texts` 是他这一局说过的全部话：开场白 + 逐轮台词 + 结局收尾，
        与复盘那一侧 `hisSpeech()` 收的是同一批（掐掉一个字都可能让某条线索
        误判成"他没提"）。

        ## 它记在谁头上

        判据是"**他**说没说过"，和 `PhoneRow.test` 的文档字符串、和复盘的
        措辞（"${TA}跟你说到了 N 条"）一致，**不声称是玩家问出来的**。
        实测证明这个区分必须守住：`tools/cue_coverage.py` 拿固定路线量过，
        某些场景的劝阻对象会在玩家什么都没问对的情况下主动抖出线索
        （liu 的 `parrot` 路线上女儿那条是 100% 主动说的）。

        ## 为什么服务端也要算一遍

        复盘那一侧算它是为了**显示**，这里算它是为了**落数**——
        客户端报上来的数字不能当指标用。两侧吃的是同一份正则（`PhoneRow.test`
        随场景下发）和同一批文本，所以按构造就该一致；真要分岔，
        分岔的是 JS `RegExp` 与 Python `re` 对同一个模式的语义，
        而这些模式全是简单的择一，没有踩到两边不同的那些语法。
        """
        whole = "\n".join(t for t in texts if t)
        return sum(1 for r in self.diggable if re.search(r.test, whole))

    def reveal(self) -> Dict[str, Any]:
        """打完之后才下发的那一份：他手机上那几条，以及每一条说明了什么。

        **只在 `ending` 事件里出现。** 理由见 `payload` 的文档字符串——
        它是这一局要挖的答案，开局就发给浏览器等于把答案印在屏幕背面。

        收款方（`payee`）也在这里，而不是在开局的 `money` 里：
        它写着「转账给 启航财经-王」——**骗局的名字就在收款方里**，
        而"这是个什么局"正是玩家要挖出来的东西。它只出现在结局那张转账凭证上，
        本来就没有任何一处需要提前拿到它。

        试水金额（`test_transfer`）同理：只有"拦下"那一档要用它算金额。
        """
        return {
            "phone": [
                {"avatar": r.avatar, "initial": r.initial, "name": r.name,
                 "time": r.time, "line": r.line, "clue": r.clue,
                 "test": r.test, "own": r.own}
                for r in self.phone
            ],
            "money": {
                "payee": self.payee,
                "test_transfer": self.test_transfer,
            },
        }

