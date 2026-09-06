"""六个骗局场景，一个文件一个。

**这个包里没有逻辑，只有数据和它的形状。** 谁是默认场景、按 id 怎么取、
串场词表怎么算——那些都在 `app/scenario.py`，它才是门面。

加第七个场景时要动的地方：在这里加一个模块、把它挂进 `ALL`，
然后照 [POSITIONING「再加场景时的清单」] 那五条走。**只有这两处**——
在此之前它是"在两千行里找到该插进去的位置"。
"""

from .ben import BEN
from .chen import CHEN
from .hang import HANG
from .liu import LIU
from .model import FactRow, PhoneRow, Scenario
from .shao import SHAO
from .zhou import ZHOU

#: 顺序即注册顺序，**第一个是默认场景**（`scenario.DEFAULT`）。
#: 它同时决定 `tools/balance_sim.py --scenario` 不带参数时的跑批顺序。
ALL = (CHEN, ZHOU, LIU, BEN, HANG, SHAO)

__all__ = [
    "ALL",
    "BEN", "CHEN", "HANG", "LIU", "SHAO", "ZHOU",
    "FactRow", "PhoneRow", "Scenario",
]
