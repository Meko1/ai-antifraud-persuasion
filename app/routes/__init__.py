"""按面分的三组路由。

| 模块 | 面 | 接口 |
|---|---|---|
| `game` | 对局 | `/api/game/start`、`/api/game/turn`、`/api/game/exit` |
| `ops` | 运维 | `/healthz`、`/readyz`、`/api/stats`、`/api/demo/stream` |
| `outcome` | 回传 | `/api/outcome/report` |

挂载顺序在 `app/main.py`，那里是唯一一处知道"这个服务由哪几块拼起来"的地方。
"""

from . import game, ops, outcome

__all__ = ["game", "ops", "outcome"]
