# 交接说明

> 给下一个会话看的，不是给评委看的。新会话请先读：本文件 →
> [CONTEXT.md](../CONTEXT.md) → [TECH-DESIGN.md](TECH-DESIGN.md) 的相关章节。
> **不需要**读完整个技术方案，按任务读对应章节即可。产品身份问题去查
> [POSITIONING.md](POSITIONING.md)；开发过程与历次决策见 `git log`，
> 本文件只记当前状态，不记过程。

## 未完成事项

- **50 并发压测要在目标服务器上验证。** 本机数不作数——这条门槛量的是
  网关并发额度，不是本机算力（[`tools/loadtest.py`](../tools/loadtest.py)）。
- **`REDIS_URL` 未配置时，复盘里的全局统计（"别人打成什么样"）与排行
  百分位整块不出现。** 配好后可用 `tools/stats_seed.py` 灌一批**造出来的**
  样本先让那一节出现，但脚本自己会打印"这不是真实对局"，且往共享 Redis
  上灌数据要加 `--yes`，建议先问过所有者。
- **真实人身安全信号词表是第一版，未经危机干预专业人士审核**
  （[`app/safety.py`](../app/safety.py) 的 `detect_real_world_risk`），
  上线前需要业务/合规确认。
- ~~**转账拦截屏（`#transfer` → 交底）还有三处 UI 待打磨**~~
  **三处已于 2026-09-07 全部做完**，留在这里是因为每一处的取舍值得记：
  - 450ms 锁定期（`HANDOFF_ARM_MS`）：色彩反馈那一半此前已补
    （`.primary-action:disabled`）；这次把锁**从原生 `disabled` 换成
    `aria-disabled`**（`opening.js` 的 `armHandoff()`）。原生 `disabled`
    不进 Tab 序也不进无障碍树，而这一面把焦点送在 `h1` 上，屏幕阅读器用户
    下一个动作就是往后 Tab——那 450ms 里这一屏唯一的出口对他**不存在**。
    代价是 `aria-disabled` 拦不住 click，`ackTransfer()` 里多了一道闸
  - 三处视觉权重：`.transfer-moves` 加了字重、字号与一道左侧色条，
    `.transfer-hook` 从 760 退到 700。**颜色一格没动**——那几个对比度
    是核过的数，调色就得重核
  - 交底面头部：`confirmTransfer()` 把 `#transferAppbar` 整条撤掉，
    `aria-labelledby` 同时改指 `#handoffTitle`（否则无障碍名会算空）。
    顺带把 44px 还给交底面，矮屏上那一面本来就紧（`cf6996e`）

## 需要项目所有者决定的问题（不要替他拍板）

- 28 个人格变体的人设文案细节（方言要不要、程度多深）
- 冒充公检法等场景的具体话术（按公开报道与常识写的，需业务侧把关）
- 投顾在账户里到底能看到什么（`SCAM_SCRIPT` 清单按常识写的，各家不完全
  一样，需业务侧确认）
- "本会话已按监管要求存档"这句提示的准确措辞
- **玩家会不会被剧本里"反击方式"台词动摇，怀疑自己是不是管太宽了。**
  ROLESafe 论文 §6.6.4 的实测风险：即使反复告知参与者"对方是受害者"，
  仍有人怀疑"这个人其实是骗子在下套"，且当场澄清也压不住这种怀疑。
  本项目每个场景的"反击方式"台词客观上会激活同一种怀疑，要不要在文案或
  复盘里专门回应，见 [EVIDENCE.md「Helper 角色有一个论文实测到、我们没
  检查过的具体风险」](EVIDENCE.md)，这里不擅自改台词语气

## 部署与运维坑（仍适用）

**只用 Redis，不用 MySQL。** 对局状态由客户端持有并签名（ADR-0003），
服务端不存任何东西；Redis 那点计数是旁路展示，丢了也不影响对局。引一个
数据库只会多一个部署期的失败点。

**平台 Redis 是共享实例，两条坑代码里已经堵上**：
1. 库号写错不会报错，只会静默丢数——`app/stats.py::force_db0` 把库号钉死
   在 0，写成别的会被强改并打 WARNING。
2. 通用键名会和别的作品对撞——所有键名带 `ai-antifraud-persuasion:` 前缀。

**密钥怎么传到部署机**：`.env` 有真实凭据，按打包规范不进 ZIP，而平台执行
生命周期脚本时不传环境变量。`start.sh` 会载入一个**跨 release 稳定**的本地
文件：

```
${AI_CREATOR_STATE_ROOT:-${XDG_STATE_HOME:-$HOME}}/.ai-antifraud-persuasion/env
```

部署机上手动建一次即可，之后每次重新部署自动带上（解压目录会被平台删掉
重建，这个目录不会）。`STATE_SIGNING_SECRET` 不用写，`install.sh` 首次安装
会自动生成一次性随机密钥、写进同一个目录，权限 600，跨版本复用。

**生命周期脚本不能假设 `HOME` 存在**——按打包契约优先级用
`AI_CREATOR_STATE_ROOT` → `XDG_STATE_HOME` → `HOME` → `/tmp`。

**干净机器模拟测试**（改动部署脚本后必须跑一遍，问题在本地项目目录里
永远测不出来，因为那里躺着一个 `.env`）：

```bash
unzip <zip> -d /tmp/deploytest && cd /tmp/deploytest && chmod +x *.sh
env -u HOME AI_CREATOR_STATE_ROOT=/tmp/plat-state bash ./stop.sh
env -u HOME AI_CREATOR_STATE_ROOT=/tmp/plat-state bash ./install.sh
env -u HOME AI_CREATOR_STATE_ROOT=/tmp/plat-state bash ./start.sh
```

**依赖版本**：fastapi 需 ≥0.141（更早版本把传递依赖 starlette 钉在有已知
漏洞的 <0.42）；四个核心依赖全是 `py3-none-any` 纯轮子，CentOS 7.9 上不会
现场编译，这条硬约束不能破。

**安全响应头按本作品实际加载的东西写，别抄模板**：只有同源一个 JS 一个
CSS，`script-src`/`style-src` 只给 `'self'`，不给 `'unsafe-inline'`；分享卡
走 canvas + blob 下载，`img-src` 要 `data: blob:`；**不加 HSTS**——平台是
`http://ip:21818` 直连、无 TLS，发 HSTS 反而会让浏览器把这个 host 记进强制
HTTPS 列表，打不开。

**网关会限流，且是按分钟窗口算的**：跑批遇到 429 时，重试退避要按分钟级
窗口设计（不能是"瞬时 5xx"那种秒级退避，否则两次重试全落在同一个限流
窗口里，退了等于没退）。跑长批之前先探一次健康检查：

```bash
.venv/bin/python -c "import asyncio;from app.llm import llm_client;print(asyncio.run(llm_client.probe()))"
```

**内网网关 token 用尽是真实会发生的事**（ADR-0007 已处理自动降级），
经验上一天的调用预算在三千多次量级，排长批时按这个数排。

## 前端结构现状

原生 HTML/JS，零构建，`package.sh` 整目录打包。

- `static/index.html` — 结构（转账确认屏 + 工作台 + 聊天页 + 复盘）
- `static/style.css` — 设计令牌与全部样式
- `static/app.js` — 入口，只串模块与挂监听；其余是若干 ES module，
  模块清单以 `tests/frontend/harness.mjs` 的 `MODULES` 为准（不在这里写
  个数——写了就要在加模块时记得改，而这件事从来没被改对过）
- 分工原则：**视图依赖状态，状态不依赖视图**

**界面语法整体照搬微信**（转账确认屏除外，那一屏刻意保持东财品牌样式），
理由见 [DESIGN.md](../DESIGN.md) 与 [TECH-DESIGN §8.1](TECH-DESIGN.md)。

**SSE 走 POST**，不能用 `EventSource`（它只发 GET），`readEvents()` 自己
解协议。`score` 事件在台词播完后才到，前端据此播放信任度动画，顺序是
刻意的，不要改。

**K 线只在复盘里，分享卡上没有它**。分享卡是复盘正文那一整块的 Canvas
出图，跟正文用同一套画法（`paintKline` 直接复用）。

## 已知限制

- **分类不是确定性的。** 现役模型上 `temperature` 已废弃，同一份标注集
  连续跑两次，准确率能相差 0.6~1.3 个百分点。任何验收门槛都要求"看两次
  跑批"，不是一次定生死；判断"改动有没有让分类变差"时，小于 1.5 个百分点
  的变化不能当信号读。
- **`grounded` 只相对上一轮那一句判定。** 玩家扎根在三轮前说过的内容上会
  被判未扎根——这是刻意的取舍（传全量历史会让分类请求随轮次线性变长，
  它在每轮的关键路径上），不是缺陷。
- **判分是可复现的，端到端不是。** 规则表是纯函数，可离线重跑；但喂给它
  的标签来自会波动的模型分类器。
- **动剧本时要把扎根漂移和 `balance_sim` 一起看**，剧本改动会系统性地
  影响玩家能引用到的具体信息量，进而影响扎根率与胜率。

## 分类器标注集怎么用

改了分类提示词就重跑一次，几毛钱的事：

```bash
.venv/bin/python -m tools.classify_eval --limit 10   # 先确认链路
.venv/bin/python -m tools.classify_eval              # 全量 + 门槛校验
```

四道门槛，任一不过退出码非零：`hit_keys` ≥ 85%、`grounded` 全样本 ≥ 80%、
`grounded` 有钥匙子集 ≥ 80%、**`grounded` 复读攻略子集 ≥ 93%**（这道最容易
被忽视：总体达标而复读攻略子集塌方是这套门槛最危险的通过方式，见
[TECH-DESIGN §9.3](TECH-DESIGN.md)）。

标注集自身的质量由 `tests/test_classify_eval.py` 守着（不调模型，进 CI）：
规模、闭集、难例覆盖、扎根比例、复读攻略必须标未扎根。**加标注前先看文件
顶部的五条约定**，尤其是"一条样本最多标一把钥匙"——蒙特卡洛的人设每轮
最多出一把钥匙，允许双钥匙会让单轮上限翻倍，§9.4 的标定直接失效。

## 踩过的坑（别再踩一遍）

1. **`delta` 语义**：只记判分结果，**不含信任流失**。
2. **取整规则**：权重相乘必出小数，实现是**求和后一次性四舍五入、对负数
   对称**（`_round_half_up`）。改这里会动摇蒙特卡洛结果。
3. **兜底台词绕过安全层直发**，所以必须自身安全。加词后跑一遍安全层自审。
4. **开场白必须进 session，而且要一路带下去**。它是第 1 轮唯一可供"扎根"
   的内容。
5. **`STATE_SIGNING_SECRET` 缺失时服务拒绝启动**。CI 里已经配了一次性值。
6. **判分参数不要抄进前端**。信任度初值与劝住阈值由 `/api/game/start`
   下发（`trust` / `win_threshold`）。抄一份到 JS 里，调参时蒙特卡洛会
   重跑，页面上那条线不会。
7. **`unzip -Z1 ... | grep -q` 在 `set -o pipefail` 下会假失败**。`grep -q`
   命中即退出，`unzip` 吃到 SIGPIPE 返回非零，整条流水线被判失败——
   `package.sh` 里已改成先取清单再用 herestring。
8. **canvas 作为 flex 子项要写 `min-width: 0`**。按 dpr 放大后的固有宽度
   会变成 min-content，把同一行右边的东西整个挤出屏幕。
9. **中文输入法的回车**：监听 keydown 发送时必须判 `e.isComposing`，
   否则玩家选候选词时会把半句话发出去。
10. **虚构设定必须与界面语法一致**。台词是打字打出来的，不是嘴上说的——
    提示词与兜底台词里不能出现"喂""挂断"这类电话用语。
11. **`requestAnimationFrame` 不能用来推进状态**。页面切到后台它就不再
    回调（`setTimeout` 也会被节流）。玩家切出去看一眼微信是常态，
    数字滚动、单据盖章、复盘曲线三处都要有不依赖 rAF 的兜底路径。
12. **测试替身用 `**kwargs` 会把"参数根本没传"藏起来。** 断言要落在
    "收到了什么"上，不是"调得通不通"。
13. **"不可降级"不等于"不需要超时"。** 降不降级是产品决定，有没有超时是
    工程底线，两件事要分开想。
14. **正则的宾语比动词重要。** 注入吸收规则只看动词（扮演/模拟/重复/忘记）
    会把"你扮演一下你女儿"这类正常追问当成攻击。这个方向上宁可漏，不可
    误伤——漏了还有输出侧那道安全层，误伤是直接惩罚正确答案，没有第二道
    能救。
15. **纯数字规则会撞上剧本自己的数字。** 六位数金额与 A 股代码同形状，
    加规则之前先问一句：这个形状在我们自己的台词里会不会出现。
16. **`grid-template-columns: 1fr auto` 在窄屏上会挤爆左列。** 需要可换行
    的内容用 flex，放不下就整条掉到下一行。
17. **给自己写的元素设了 `display`，`hidden` 属性就会失效**——
    `[hidden]` 的 `display:none` 来自浏览器 UA 样式表，优先级压不过类
    选择器上写死的 `display:flex`，需要显式补 `[hidden] { display: none }`。
18. **`.disposal b` 之类的语义标签可能被全局样式改成块级**——说明文字里
    的行内标签要检查全局选择器有没有把它顶成独立一行。
