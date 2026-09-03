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


# 取词时往回避开几句。**2 是实测的拐点**，表在 `fallback_line` 的 docstring 里：
# 1 只压得住紧邻重复，2 连"隔一轮又说同一句"一起压掉，3 一句都不多赚。
#
# 放在这儿而不是 offline.py：两条降级路径（离线网关与 engine 的 L1）都要用它，
# 而 **engine 不许 import offline** —— `tests/test_offline.py` 钉着"离线网关
# 只在 OFFLINE_DEMO 打开时才装上"，让主链路依赖那个模块等于把那条界线抹掉。
AVOID_WINDOW = 2


def fallback_line(
    mood: Mood,
    rng: Optional[random.Random] = None,
    scene: Optional[object] = None,
    avoid: str = "",
) -> str:
    """按档位取一条台词，**避开最近说过的那几句**。

    `avoid` 传劝阻对象最近几轮说过的话拼在一起（`TurnRecord.reply`），
    用来挡住**看得出来的重复**。

    ## 为什么这一条值得单独写一段

    原来是无记忆的 `random.choice`，每档 10 条。玩家在同一个档位上连坐
    三五轮是常态（阻力曲线本来就要求这样），于是——

        一局 12 轮里出现「上一句原样重复」的概率：56.9%
        （每档停留 3 轮的典型走法，蒙特卡洛 20000 次；实测一局撞了两次）

    ## 窗口为什么是 2 而不是 1

    只排除上一句，紧邻重复归零，但"隔一轮又说同一句"照样刺眼。实测：

        排除窗口   紧邻重复    3 轮内重复   一局里不同的台词
        0（原状）    56.9%      72.9%       10.8 / 12
        1           0.0%      37.4%       11.6 / 12
        2           0.0%       0.0%       12.0 / 12
        3           0.0%       0.0%       12.0 / 12

    **2 是拐点**，再往上一句都不多赚。每档 10 条，排除 2 条还剩 8 条可选，
    离"退化成固定轮播"还很远——那是这个方向上唯一要防的另一头。

    这个作品唯一无法被复制的资产是「劝阻对象是活的」
    （POSITIONING「不做什么」第一条）。**紧邻的逐字重复是唯一能一秒钟
    推翻这句话的东西**，而离线模式正是路演要用的那个模式（README）。
    判分不受影响——但没有人会先看判分，他们先看那两句一模一样的话。

    ## 判据为什么是 `in` 而不是 `==`

    `avoid` 是若干条 `"".join(spoken)` 拼起来的，而 `spoken` 是按句缓冲切过
    再拼回来的，不保证与台词库里那一条逐字节相等。用 `in` 是宽的那一侧：
    宁可多排除一条候选（还剩 8 条），也不要因为差一个标点就漏掉一次重复。

    全部排除掉的话（`avoid` 把整档吃干净，10 条里不可能，但便宜）
    退回全集——**这个函数在任何情况下都必须给得出一句话**，
    它本来就是"绝不给玩家一片空白"那一层。
    """
    # 调用方拼几句由它自己决定，这里不碰历史——`fallback_line` 是纯函数，
    # 让它去认识 TurnRecord 就把一个取词函数变成了半个引擎
    lines = list((scene or _default()).lines[mood])
    if avoid:
        rest = [line for line in lines if line not in avoid]
        if rest:
            lines = rest
    return (rng or random).choice(lines)


def ending_fallback(ending: Ending, scene: Optional[object] = None) -> Sequence[str]:
    """结局台词的兜底。不随机——最后一屏要的是确定，不是花样。"""
    return (scene or _default()).ending_lines[ending]


def _default():
    # 局部导入避免 scenario ← persona ← （无）与 fallback 之间绕出循环依赖
    from .scenario import DEFAULT

    return DEFAULT
