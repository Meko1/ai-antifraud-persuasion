"""分类结果解析。

分类请求是唯一一次让模型对玩家发言下判断的地方，但它只输出标签——
提示词里根本不存在"分数"这个概念，谄媚因此无处施力（ADR-0001）。

解析在闭集上做：模型编出来的标签一律丢弃。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Tuple

from .scoring import ALL_PENALTIES, KEY_VALUES

# 闭集：钥匙 + 失误（含合规红线）。判分引擎只认这些标识。
LABELS = frozenset(KEY_VALUES) | frozenset(ALL_PENALTIES)


@dataclass(frozen=True)
class Classification:
    hit_keys: Tuple[str, ...]
    grounded: bool
    # 模型说它扎根在哪一句上（P1-12）。**不参与判分**，只进语料。
    #
    # 不拿它去推翻模型自己的 grounded 判断，这是个有意的取舍：
    # 验证只能做字符串比对，而模型引用时常常是转述而非原文。用一个模糊匹配
    # 去否决一个判断，等于引入一类新的静默误判——那正是 P1-11 刚修掉的病。
    #
    # 它的用处在**标注环节**：人工复核这批语料时，"模型认为它引用了哪一句"
    # 是判断这条标签对不对的最快线索。所以它落进语料，不落进分数。
    evidence: str = ""


# 允许出现的键。**多一个就拒绝解析**，理由见 `parse_classification` 的文档。
_SCHEMA_KEYS = frozenset({"hit_keys", "grounded", "evidence"})

# `evidence` 的长度上限。它是一句引文，不是一段论述——
# 模型偶尔会在这个字段里写整段分析，截断它省得把语料撑爆
_MAX_EVIDENCE = 120


def parse_classification(raw: str) -> Optional[Classification]:
    """解析模型返回的分类结果。无法解析时返回 None，由调用方决定重试或降级。

    ## 严格到什么程度，为什么

    原先这里是 `bool(data.get("grounded", False))`。它对 Python 是对的，
    对**模型输出**是错的：模型返回 `"grounded": "false"` 时，
    `bool("false")` 是 `True`——一句没有扎根的话被判成扎根，
    直接改变这一轮的分数（扎根与否是 0.45 倍的折扣）。实测复现过。

    同一个洞的第二个形态是 `hit_keys` 不去重：模型返回
    `["support_autonomy", "support_autonomy"]`，判分那边会把同一个动作
    算两遍分，统计那边会把命中数记两次。

    所以这里改成闭集 + 严格类型，三条：

    1. `grounded` 必须是**真正的 JSON 布尔**。字符串、数字、null 一律拒绝——
       不做 `"true"→True` 的转换。能容错的地方就是会漂移的地方，
       而这里漂移的代价是分数错得没人看得见。
    2. `hit_keys` 必须是字符串列表，**按首次出现去重**，闭集外的丢弃。
       去重保留顺序：消歧规则 1 说"取最主要的那个动作"，第一个就是那个。
    3. **出现未知键就整条拒绝。** 这一条是为了让格式漂移**响**：
       模型某天开始返回 `{"labels": [...]}`，宽松解析会安静地得到空标签，
       每一轮都判 0 分，而没有任何一处会报错。拒绝解析会走降级路径，
       降级是有日志、有 `degraded` 标记、会被统计排除的。

    拒绝之后返回 None，调用方（`engine._classified`）按中性判分并置 `degraded`。
    **这比猜一个值好**：猜错的那一轮会伪装成"玩家说了句没用的话"。
    """
    try:
        data = json.loads(_strip_fence(raw))
    except (ValueError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    # 未知键 → 格式漂移，整条拒绝（理由见上面第 3 条）
    if not set(data).issubset(_SCHEMA_KEYS):
        return None

    hits = data.get("hit_keys")
    if not isinstance(hits, list):
        return None
    if not all(isinstance(h, str) for h in hits):
        return None

    # `grounded` 缺省允许（当成 false），但**给了就必须是真布尔**。
    # `isinstance(True, int)` 在 Python 里是真，所以不能用 int 兜——
    # 这里判的就是 bool 本身。
    grounded = data.get("grounded", False)
    if not isinstance(grounded, bool):
        return None

    # `evidence` 是可选的诊断字段。类型不对就当没给——**它不该有能力
    # 让整条分类作废**，因为它不参与判分（见 Classification.evidence）。
    evidence = data.get("evidence", "")
    if not isinstance(evidence, str):
        evidence = ""

    return Classification(
        hit_keys=_dedup(h for h in hits if h in LABELS),
        grounded=grounded,
        evidence=evidence.strip()[:_MAX_EVIDENCE],
    )


def evidence_present(evidence: str, pool: str) -> Optional[bool]:
    """模型给的那句引文，在它能看到的对话里找得到吗。

    返回 None 表示"没法判"（模型没给引文，或者没有可比对的池子）——
    **和"找不到"不是一回事**，混成一个值的话，语料里就分不清
    "模型编了一句" 和 "模型什么都没说"。

    判据是宽松的子串匹配：去掉标点与空白之后，引文的任意一段连续 6 个字
    出现在池子里就算找到。要求整句原样出现会把绝大多数正常引用判成编造——
    模型引用时几乎总是转述。

    结果只进语料、只写日志，**不改分数**，理由见 `Classification.evidence`。
    """
    if not evidence or not pool:
        return None
    strip = str.maketrans("", "", " \t\n　，。！？、：；「」“”‘’,.!?:;\"'()（）")
    needle = evidence.translate(strip)
    hay = pool.translate(strip)
    if not needle or not hay:
        return None
    if len(needle) <= 6:
        return needle in hay
    return any(needle[i:i + 6] in hay for i in range(len(needle) - 5))


def _dedup(labels) -> Tuple[str, ...]:
    """按首次出现去重。顺序有意义——消歧规则 1 取的是"最主要的那个动作"。"""
    seen = set()
    out = []
    for label in labels:
        if label not in seen:
            seen.add(label)
            out.append(label)
    return tuple(out)


def _strip_fence(raw: str) -> str:
    """要求 JSON 输出，模型照样会包一层 ```json——这是常态，不是异常。"""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if body.lower().startswith("json"):
        body = body[4:]
    return body.rsplit("```", 1)[0].strip()
