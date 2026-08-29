---
target: "冷开场银证转账屏 (static/index.html #transfer)"
total_score: 21
max_score: 36
na_heuristics: 7
p0_count: 1
p1_count: 5
timestamp: 2026-08-29T10-00-21Z
slug: static-index-html
---
Method: dual-agent (A: design review · B: detector + browser evidence)

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|---|---|---|
| 1 | Visibility of System Status | 2 | 换面 0ms 硬切，无任何系统响应信号；appbar 换面后仍写「银证转账」，与 h1 矛盾 |
| 2 | Match System / Real World | 2 | 「持仓已于 09:32 全部卖出」+ 同日转出 30 万 = T+1 违例 |
| 3 | User Control and Freedom | 2 | 看过一次永久回不来（键在 sessionStorage，localStorage.clear() 无效） |
| 4 | Consistency and Standards | 3 | .primary-action 不在强焦点环名单；.handoff-body 用了本仓明令禁止的 justify-content:center |
| 5 | Error Prevention | 1 | 两按钮共用同一像素矩形 [19,716,337,52]，零延迟零禁用 |
| 6 | Recognition Rather Than Recall | 3 | face 2 抹掉金额与账户，却要用户凭记忆理解「这一帧」 |
| 7 | Flexibility and Efficiency | n/a | 每会话只演一次，刻意不提供加速器 |
| 8 | Aesthetic and Minimalist Design | 3 | face 2 第一段是写给评委的论证，稀释 h1 到橙色落点的张力 |
| 9 | Error Recovery | 3 | 无输入即无错误路径；唯一可发生的失败（手滑）没有恢复 |
| 10 | Help and Documentation | 2 | 唯一的"这是什么"是 11.5px / 3.50:1 的 .transfer-sim，face 2 上整行消失 |
| **Total** | | **21/36** | **Acceptable (58%)** |

n/a: #7（每会话单次，无熟练用户路径）。#3 由 A 的 1 上调至 2：页脚已写「随时可以退出」，单向路径是设计选择；sessionStorage 不可逆才是真问题。

## Design Specificity Verdict

face 1 的通用感正是作品性——克制到没有一处可欣赏，才买得到那 10 秒的相信。但论据没有被排版兑现：.transfer-rule（12px 灰色右对齐）与 .transfer-moves（12px）是全屏最不可替换的两行，视觉权重却低于通用字段值（15px/700）。

确定性扫描：按标准命令跑 detect.mjs 得 exit 0 假阴性，两个成因——skill 目录缺 node_modules（退化为正则）、index.html 的 href="static/style.css" 按服务端布局写（/static 挂载于 app/main.py:734），以文件路径喂检测器解析成 static/static/style.css。补齐后真实 exit 2 / 20 条，in-scope 11 条。低对比 4 条经浏览器独立复现（数值吻合到小数点后两位），非误报；pulsing-dot / repeating-stripes / cream-palette / cramped-padding 为对本目标误报或范围外。

覆盖层：未注入。应用 CSP script-src 'self' 拦下跨源 detect.js（这一拦是应用做对了，与就绪度审计 P0-1 的 frame-ancestors 是两回事）。

## What's Working

1. 两面同处一个 <section>，appbar 与 footer 几何完全不动（两按钮实测同为 [19,716,337,52]）。拦截落在刚签过的那张纸上。
2. face 1 主动放弃表现欲：一个 38px 数字、三行 tabular-nums 核对表、发丝线，无卡片无图标无插画。
3. 单一橙色纪律：--brand-orange 全屏只落 CTA 和最后那句，视线被控制在 160px 内。
4. 零横向溢出（375/320/1440）、零控制台报错、按钮 52px 达 WCAG 2.5.5。

## Priority Issues

**[P0] 双击可以把整个转向永久删掉** — static/opening.js:71
130ms 双击：第一下 transferGo，第二下命中已就位的 handoffGo，直接落到工作台，face 2 一帧没渲染；ackTransfer() 已写 sessionStorage，刷新回不来。
修：confirmTransfer() 里给 #handoffGo 加 disabled，约 450ms 后解禁（reduced-motion 下设 0）。这 450ms 顺便是转向需要的那一拍呼吸。
命令：$impeccable harden

**[P1] face 2 对屏幕阅读器完全静默** — static/index.html、static/opening.js:77
#transfer 子树内 aria-live/role=status/role=alert 数量为 0；焦点直接送到 #handoffGo，越过 h1 和三段正文。SR 用户听到的唯一一句是「坐到对面，按钮」，有理由认为转账成功了。
修：#transferHandoff 加 role="status"；焦点改送 h1（tabindex="-1"）。
命令：$impeccable harden

**[P1] T+1 违例** — static/index.html:63
「持仓已于 09:32 全部卖出」+ 时钟 14:46 + 本笔 30 万，正是 app/scenario.py:424 判定"走不通"的写法。8-29 那批把五个场景全改成「昨天全部卖出」，这一屏漏了。
修：改「持仓昨日已全部卖出」。

**[P1] 「有人正要做同样的事」对五分之四的访客不成立** — static/index.html
face 1 数字硬编码照老陈写，客户由 app/trigger.py:141 scenario_for_trigger(trigger, anomaly_id) 按异动分配。实测抽到刘卫东时金额、品种、信号三项全不一致。
修：要么 face 1 跟着真实场景渲染，要么把 face 2 换成经得起五分之五的说法。
命令：$impeccable clarify

**[P1] 四处对比度不达 AA** — 检测器与浏览器双确认
.handoff-turn b（整个作品那句话）3.36:1；.primary-action ×2 3.64:1；.transfer-sim 3.50:1。16px/760 未达 18.66px bold，不适用 3:1 豁免。.transfer-rule 5.03、.transfer-moves 5.36 合格。
命令：$impeccable colorize

**[P1] 最重要的两个按钮拿到最弱的焦点环** — static/style.css:2044
全局 :focus-visible rgba(36,61,79,.28) 合成后 #bcc2c4：对纸底 1.66:1、对橙按钮 2.02:1，低于 WCAG 2.2 SC 1.4.11 的 3:1。强环名单为 .sheet/.chat-exit/.composer-help/.ghost-action，不含 .primary-action。
命令：$impeccable harden

**[P2] 矮屏上落点句被横切** — static/style.css:1545
320×568：face 1 有 141px 在折线下（.transfer-moves 与 .transfer-sim 完全不可见，无渐隐无滚动提示）；face 2 有 45px 在折线下，.handoff-turn 被部分裁切。.handoff-body { justify-content: center } 正是 style.css:1579 注释论证过不能用、已改成 ::before/::after { flex:1 1 0 } 的写法。375×812 与 320×812 都不需要滚动——是视口高度问题不是宽度问题。
命令：$impeccable adapt

**[P2] 「模拟界面」那行在两处失效** — static/index.html
(a) 它是 overflow-y:auto 容器最后一个子元素，矮屏折在线下；(b) face 2 上随 #transferForm[hidden] 一起消失，而那正是用户被要求投入三分钟的时刻，就绪度审计 P1-5 的修法在这一面被撤销。
修：把 <p class="transfer-sim"> 提出来挂进 .primary-foot（flex:none）或挂到 <section> 下两面共用。一处改动解决两个失效。

## Persona Red Flags

Sam（屏幕阅读器/键盘）：换面零播报，焦点越过全部正文；焦点环 1.66:1；face 1 零 heading（appbar 标题是 <strong>，#transfer 无 aria-labelledby）；.transfer-rule/.transfer-moves 与所限定的 <dd> 无编程关联。

Casey（单手手机）：拇指压在原位就吃到 P0 穿透；320 宽下「本人银行卡 · 瑞通银行 ****4407」折两行，尾号孤立在第二行末尾（行高 56→75px），而按钮下那句正好是「请核对转入账户信息」。

Riley（压力测试）：130ms 双击穿透且写入 sessionStorage，不可复现；矮屏顶部裁切不可上滚；反复刷新后这一屏永远不再出现，localStorage.clear() 没用。

## Minor Observations

- 中文正文三处半角空格（源码换行+缩进折叠）：「最后一帧。 它下一秒」「安全账户"。 拦下他们」「坐到对面—— 有人正要做」。
- "安全账户" 用 ASCII 直引号，中文应为「」或 “”。
- 深色模式零处理，style.css:2 用 color-scheme: light 钉死。这是对的（模拟 iOS 浅色 App），但要清楚是主动选择。
- reduced-motion 全局规则重复两遍（style.css:1229 与 2075，内容完全相同）。
- 按钮下 boilerplate「请核对转入账户信息」可换成「实时到账，提交后不可撤销」——等长、更像真的，并把 face 2 的前提提前埋进 face 1。本屏性价比最高的作者性改动。
- appbar 在 face 2 上仍写「银证转账」，与 h1 矛盾。主张保留（换掉就变成两块不相干的屏），但是已知负债。

## Questions to Consider

1. 转向的全部力量来自"我刚签了字"，为什么 face 2 第一件事就是把那张单子整个抹掉？把 ¥300,000 与「仅支持转入本人同名银行卡」降透明度留在 h1 上方，代价为零，「这一帧」就真的有一帧可指。
2. 「只能转给自己」100% 写在 face 2 的说明文里、0% 写在 face 1 的版式里。如果它是论据，为什么权重低于「我的资金账户 ****5106」？
3. 「有人正要做同样的事」——改成真的（face 1 跟场景走），还是改成不必为真（换个经得起五分之五的说法）？
