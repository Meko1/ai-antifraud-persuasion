"""兜底台词库。

降级时按情绪档位取词，玩家未必察觉：骗子本来就说车轱辘话。

开场白原本也在这里（同样是预生成的，「首屏 ≤3 秒」靠它买单），
2026-08-15 搬去了 app/persona.py：开场自称什么，后面十二轮就得是什么，
所以它必须跟人格变体同源。不调模型这一条没有变。

**这些台词绕过输出安全层直接下发**（它们是人工审过的），因此必须写得
本身就安全：不出现任何真实股票代码、上市公司名、百分比+时间窗的收益承诺、
联系方式或链接。角色一律把标的称作"那只票"。

规格见 docs/TECH-DESIGN.md §6.3。
"""

from __future__ import annotations

import random
from typing import Mapping, Optional, Sequence

from .scoring import Ending, Mood

# 台词库**全部搬进了场景**（app/scenario.py 的 `lines` / `ending_lines` /
# `waiting`）。搬走的理由和演绎指示一样：这四十条讲的是一个被荐股群套住的人，
# 换个场景一句都不能用。这里只留取词的规则。
#
# 下面三个函数的 `scene` 都可以不传，落到默认场景——测试替身与旧调用不必
# 知道场景这回事。


def fallback_line(
    mood: Mood, rng: Optional[random.Random] = None, scene: Optional[object] = None
) -> str:
    lines = (scene or _default()).lines[mood]
    return (rng or random).choice(list(lines))


def ending_fallback(ending: Ending, scene: Optional[object] = None) -> Sequence[str]:
    """结局台词的兜底。不随机——最后一屏要的是确定，不是花样。"""
    return (scene or _default()).ending_lines[ending]


def waiting_line(scene: Optional[object] = None) -> str:
    """排队期间前端显示的等待文案，角色兼容，不是转圈加载。"""
    return (scene or _default()).waiting


def _default():
    # 局部导入避免 scenario ← persona ← （无）与 fallback 之间绕出循环依赖
    from .scenario import DEFAULT

    return DEFAULT
