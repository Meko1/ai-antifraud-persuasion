"""数据出处：这条记录是谁、用哪一版规则、在什么模式下产生的。

一个模块管三件本来分散的事，因为它们回答的是同一个问题的三个侧面：

| 侧面           | 字段                          | 少了它会怎样                       |
|----------------|-------------------------------|------------------------------------|
| **哪一版**     | `*_VERSION`                   | 提示词改完之后，命中率变了说不清是模型变了还是规则变了 |
| **哪种模式**   | `data_mode`                   | 离线演示的关键词分类结果混进线上命中率 |
| **标签谁给的** | `label_source` / `review`     | 拿模型自己的预测去训模型自己        |

## 为什么版本要写死在代码里而不是读 git

留存的语料会活得比这个仓库的任何一次 checkout 都久。一条记录如果只写
`commit=1265b0f`，三个月后要复现它，得先找到那个 commit、还得指望那时候
仓库还在。写死的语义化版本号是**这条记录自己带着的**，不依赖任何外部系统。

代价是改了规则要记得改这里。这个代价由测试兜住：
`tests/test_provenance.py` 把版本号和它所描述的那张表钉在一起，
改了表不改版本号，测试会红。

## `label_source` 那一列最要紧

`hits` 和 `grounded` 现在直接来自分类器——**它们是模型的预测，不是事实**。
不标出来的话，下一个人拿这批语料去训一个新分类器，训出来的是
"更像当前分类器的分类器"，而当前分类器的偏差会被原样放大一遍。
标注生命周期（预测 → 待审 → 专家标注 → 争议复核 → 金标）里，
目前落在第一格；这个字段的作用是**让它诚实地待在第一格**。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict

# ── 版本 ──────────────────────────────────────────────────────────────────

# 判分规则表（app/scoring.py 的 KEY_VALUES / PENALTY_VALUES / COMPLIANCE_VALUES /
# EFFICACY / 钝化 / 蓄势池 / 结局阶梯）。**任何一个数动了都要 +1。**
# 蒙特卡洛门槛跟着这个版本走：换了版本，历史分布就不能再和新分布比。
RULES_VERSION = "2026.08.17"

# 演绎与分类提示词（app/gateway.py 的两段 SYSTEM_PROMPT）。
# 分类提示词是命中率的直接决定因素——它变了，历史命中率就不可比。
PROMPT_VERSION = "2026.08.23"

# 场景内容（app/scenario.py 的五个 Scenario）。改台词、改档案、改揭晓清单都算。
SCENARIO_VERSION = "2026.08.23"

# 分类结果的解析口径（app/classify.py）。严格 schema 生效之后，
# 同一份模型输出解析出来的结果与之前不同，因此它需要自己的版本号。
CLASSIFIER_VERSION = "2026.08.23-strict"


class DataMode(str, Enum):
    """这条记录是怎么产生的。

    **默认排除在业务统计和训练语料之外的是 `OFFLINE_DEMO`。**
    离线演示用的是关键词分类器和预置台词——它的命中率反映的是规则表写得全不全，
    不是模型判得准不准。两者混在一起算，得到的数谁也不代表。
    """

    LIVE = "live"
    OFFLINE_DEMO = "offline_demo"


class LabelSource(str, Enum):
    """`hits` / `grounded` 这两列是谁给的。"""

    MODEL = "model"              # 大模型分类器的预测
    KEYWORD_RULE = "keyword_rule"  # 离线演示的关键词规则
    DEGRADED_NEUTRAL = "degraded_neutral"  # 分类失败，按中性记的空标签
    HUMAN_GOLD = "human_gold"    # 专家标注确认过的金标（当前系统还产生不了）


class ReviewState(str, Enum):
    """标注生命周期。当前系统只产生 `PENDING`，其余几档是给后续工作流留的位置。

    留着它们不是画饼：字段现在就写进每条记录，将来接标注工作流时
    历史数据不需要迁移——它们本来就带着一个诚实的 `pending`。
    """

    PENDING = "pending"        # 模型预测，没人看过
    IN_REVIEW = "in_review"    # 已进入标注队列
    DISPUTED = "disputed"      # 标注人之间不一致，待复核
    GOLD = "gold"              # 金标确认，可进训练集


def versions() -> Dict[str, str]:
    """一条记录要带的全部版本。"""
    from . import APP_VERSION

    return {
        "app": APP_VERSION,
        "rules": RULES_VERSION,
        "prompt": PROMPT_VERSION,
        "scenario": SCENARIO_VERSION,
        "classifier": CLASSIFIER_VERSION,
    }


def provenance(*, offline: bool, degraded: bool) -> Dict[str, Any]:
    """一条语料记录的出处段。

    `degraded` 优先于 `offline`：分类失败那一轮的空标签既不是模型给的，
    也不是关键词规则给的，它是**程序按中性填进去的**。把它记成 `model`，
    等于往训练集里塞一批"模型认为这句话什么都不是"的假样本。
    """
    if degraded:
        source = LabelSource.DEGRADED_NEUTRAL
    elif offline:
        source = LabelSource.KEYWORD_RULE
    else:
        source = LabelSource.MODEL

    return {
        "data_mode": (DataMode.OFFLINE_DEMO if offline else DataMode.LIVE).value,
        "label_source": source.value,
        "review": ReviewState.PENDING.value,
        "versions": versions(),
    }


def trainable(entry: Dict[str, Any]) -> bool:
    """这条记录能不能进训练集。

    三条全要满足，任何一条不满足都不进：线上模式、模型给的标签、金标确认过。
    当前系统一条都产生不了金标——**这正是它该返回 False 的原因**，
    而不是一个需要绕过去的麻烦。
    """
    return (
        entry.get("data_mode") == DataMode.LIVE.value
        and entry.get("label_source") == LabelSource.MODEL.value
        and entry.get("review") == ReviewState.GOLD.value
    )
