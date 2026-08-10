# 交接说明

> 写于 2026-08-10。给下一个会话看的，不是给评委看的。
> 新会话请先读：本文件 → [CONTEXT.md](../CONTEXT.md) → [TECH-DESIGN.md](TECH-DESIGN.md) 的相关章节。
> **不需要**读完整个技术方案，按任务读对应章节即可。

## 当前状态

后端端到端可玩，**82 个测试全绿**（`python -m pytest`，约 8 秒）。真实模型已接通并验证。

```
.venv/bin/python -m pytest                    # 全部测试
.venv/bin/python -m tools.balance_sim         # 蒙特卡洛，2 万局/人设
STATE_SIGNING_SECRET=x ./start.sh             # 起服务（.env 里已配好，可直接 ./start.sh）
```

### 已完成

| 模块 | 文件 | 说明 |
|---|---|---|
| 判分引擎 | `app/scoring.py` | §3.4 十步求值顺序、三道调节机制、蓄势池、结局判定、情绪档位 |
| 输出安全层 | `app/safety.py` | §5.2 五类规则 + §5.3 注入吸收 |
| 按句缓冲 | `app/streaming.py` | 句末标点 + 字符阈值强切 |
| 状态令牌 | `app/state_token.py` | HMAC 签名、2 小时过期、history、开场白 |
| 分类解析 | `app/classify.py` | 闭集过滤、代码围栏容错 |
| 对局编排 | `app/engine.py` | 并行双调用、L1 降级、事件流 |
| 网关适配 | `app/gateway.py` | 演绎/分类/结局生成三操作 + 提示词 |
| 兜底台词 | `app/fallback.py` | 每档 10 条 + 开场白 6 条 + 通用反击句 |
| HTTP | `app/main.py` | `/api/game/start`、`/api/game/turn`(SSE) |
| 蒙特卡洛 | `tools/balance_sim.py` | §9.4 门槛入 CI |

### 未完成（按建议顺序）

1. **§9.3 分类器标注集**（50–100 条）—— 见下方"任务 A"
2. **前端三件**：对局页、复盘面板、分享卡 —— 见下方"任务 B"
3. `/api/stats` 与 Redis 旁路统计（§7.1、§10.2）
4. 压测：50 并发下首句延迟 P95 与降级触发率（§10.3）

---

## 任务 A · 分类器标注集（§9.3）

**目标**：手工标注 50–100 条玩家发言 → 期望 `hit_keys` 与 `grounded`，跑准确率与混淆矩阵。
**门槛**（§9.4）：`hit_keys` 准确率 ≥ 85%，`grounded` 准确率 ≥ 80%。

**起点**：

- 闭集定义在 `app/scoring.py` 的 `KEY_VALUES` / `PENALTY_VALUES`
- 分类提示词在 `app/gateway.py` 的 `CLASSIFY_SYSTEM_PROMPT`
- 解析在 `app/classify.py::parse_classification`
- 标注集建议放 `tests/data/classification_set.jsonl`，跑批脚本放 `tools/classify_eval.py`
- §9.3 要求刻意覆盖三类难例：语义命中但措辞刁钻、字面像钥匙但实为说教、复读攻略（`grounded=false`）

**注意**：跑批要真实调用模型，会花钱。建议标注集先写 50 条，跑通了再补。
准确率跑批**不要**放进 `pytest`（CI 里不该调外部 API），单独一个脚本，人工触发。

## 任务 B · 前端（§8）

原生 HTML/JS，零构建。现有 `static/index.html` 只是骨架期的验证页，可以整个重写。

**需要读的**：`TECH-DESIGN.md` §7.3（SSE 事件契约）、§8（前端三件的内容要求）。

**SSE 事件顺序恒定**：`meta → sentence* → score → ending? → state → done`，错误统一 `event: error`。
`score` 在台词播完后才下发，前端据此播放信任度条动画——这个顺序是刻意的，别改。

**对局页**：消息流、剩余轮次（不要真实倒计时，避免制造焦虑）、信任度条、输入框。
**复盘面板**：数据全部来自最后一个 `state` 事件令牌里的 `history`，服务端不存任何东西。
需要解析令牌的 payload 部分（`base64url(json).签名`，前端只读不验签）。
**分享卡**：Canvas 出图。

**关键**：每轮请求要带上一轮返回的 `token`，服务端无会话。

---

## 踩过的坑（别再踩一遍）

1. **`delta` 语义**：只记判分结果，**不含信任流失**。依据是 §7.2 样例里 `d:20` 恰等于基值。
2. **取整规则**：权重相乘必出小数，实现是**求和后一次性四舍五入、对负数对称**（`_round_half_up`）。
   改这里会动摇蒙特卡洛结果，§3.5 脚注那条测试会红。
3. **兜底台词绕过安全层直发**，所以必须自身安全。`app/fallback.py` 顶部有说明，
   加词后跑一遍安全层自审。
4. **开场白必须进 session**。它是第 1 轮唯一可供"扎根"的内容。曾经漏了，
   导致玩家开局说得再贴切也判 `grounded=false`。回归测试：
   `tests/test_engine.py::test_开场白与历史都要送到网关手里`。
5. **`STATE_SIGNING_SECRET` 缺失时服务拒绝启动**。CI 里已经配了一次性值。
6. **`.env` 里有真实 API key**，已被 gitignore 和 `package.sh` 双重排除。
   比赛结束后建议轮换。

## 与技术方案文档的差异

`TECH-DESIGN.md` §9.1/§9.2 的数值表已按实际跑批结果更新过一次（加权总体
37.4%→37.1%，无门控加权 50.1%→73.7%）。人设命中概率原先没有任何地方记录，
现在定义在 `tools/balance_sim.py` 里，表格与产生它的代码不会再走散。

**仍未验证的门槛**：分类器准确率（任务 A）、首句延迟 P95（需压测）。
单次实测首个增量 0.94s，但那是单请求、无并发的数字，不能当 P95 用。
