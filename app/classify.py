"""分类结果解析。

分类请求是唯一一次让模型对玩家发言下判断的地方，但它只输出标签——
提示词里根本不存在"分数"这个概念，谄媚因此无处施力（ADR-0001）。

解析在闭集上做：模型编出来的标签一律丢弃。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Tuple

from .scoring import KEY_VALUES, PENALTY_VALUES

# 闭集：钥匙 + 失误。判分引擎只认这些标识。
LABELS = frozenset(KEY_VALUES) | frozenset(PENALTY_VALUES)


@dataclass(frozen=True)
class Classification:
    hit_keys: Tuple[str, ...]
    grounded: bool


def parse_classification(raw: str) -> Optional[Classification]:
    """解析模型返回的分类结果。无法解析时返回 None，由调用方决定重试或降级。"""
    try:
        data = json.loads(_strip_fence(raw))
    except (ValueError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    hits = data.get("hit_keys")
    if not isinstance(hits, list):
        return None

    return Classification(
        hit_keys=tuple(h for h in hits if h in LABELS),
        grounded=bool(data.get("grounded", False)),
    )


def _strip_fence(raw: str) -> str:
    """要求 JSON 输出，模型照样会包一层 ```json——这是常态，不是异常。"""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if body.lower().startswith("json"):
        body = body[4:]
    return body.rsplit("```", 1)[0].strip()
