---
target: 对局主链路五屏 (opening/客户档案/对局页/两个面板)
total_score: 30
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
timestamp: 2026-08-30T00-19-05Z
slug: static-index-html
---
⚠️ DEGRADED: single-context (两个子代理均被账号会话额度中断 · 429 rate_limit)

扫描范围：对局主链路五屏 —— #opening 工作台派单、换一位客户面板、#home 客户档案、
#chat 对局页、七种问法面板。**排除** #transfer（已于同日单独 critique 并修完）与 #review。
本轮 CLI 检测器未跑（需先复刻依赖 + 服务端目录布局，否则 exit 0 为假阴性）；
证据全部来自浏览器实测与源码阅读。

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|---|---|---|
| 1 | Visibility of System Status | 3 | mood/moodHint/thread 三个 aria-live 齐备；#home 无 live region |
| 2 | Match System / Real World | 2 | 档位提示对女性客户说「他」，与两行之下的输入框自相矛盾 |
| 3 | User Control and Freedom | 3 | 退出常驻、面板 Esc 可关；退出按钮仅 31px 高 |
| 4 | Consistency and Standards | 3 | 面板是共享实现；强焦点环上一轮只覆盖 .primary-action/.deskcta |
| 5 | Error Prevention | 3 | 空输入禁发、安全提示常驻、退出二次确认 |
| 6 | Recognition Rather Than Recall | 3 | 七种问法面板键盘拿不到内容 |
| 7 | Flexibility and Efficiency | 2 | 无快捷键，无法回看上一轮档位提示 |
| 8 | Aesthetic and Minimalist Design | 4 | 对话页与工作台都干净克制 |
| 9 | Error Recovery | 3 | #assignment 有重连路径；未实测断网中途 |
| 10 | Help and Documentation | 4 | 课程表 + 问法面板 + 档位提示三层 |
| **Total** | | **30/40** | **Good** |

**与上一份（#transfer 21/36）不可直接比较：扫的不是同一批屏。**

## Priority Issues

**[P1] 档位提示对五分之三的客户说错性别** — static/keys.js:118
MOOD_HINTS 四条提示、八处「他」全部写死。实测林月娥一局同屏出现「他想尽快结束」与
「输入你想对她说的话」。周淑琴/林月娥/顾之然三位是「她」——六成对局每一轮都错。
代词管道本身完好（opening.js:298、review.js 全程用 peerPronoun()），只有 8-25 新加的
MOOD_HINTS 绕过了它。app/scenario.py 注释原话：「代词不是细节」。
修：四条提示改成取 peerPronoun() 的模板。

**[P1] 七种问法面板，键盘用户只拿得到「取消」** — static/sheet.js:52
面板本身 role=dialog / aria-modal / 焦点陷阱 / Esc / 焦点归还五件齐全，做得比多数生产
代码好。但七个选项是 DIV（注释解释了理由，且理由成立），于是打开后焦点落在
.sheet-cancel，整个面板唯一 Tab 停靠点就是取消；面板还溢出 57px（707/650）。
修：选项保持非按钮，把 .sheet-list 做成 tabindex="-1" 容器并在打开时接管焦点。

**[P1] 六处对比度不达 AA**
#moodHint 3.50（11.5px）/ #say::placeholder 3.41 / #send 3.64（22px/500，不算大字号）/
#mood 4.46 / .live-label 4.46 / .opening-hint 4.46。
后三条同为 --signal #bd4f40 落纸底，保色相压到 #bb4e3f = 4.54；
#moodHint 的 --quiet #7d8580 压到 #6b726e = 4.56。
档位提示这条尤其值得修：HANDOFF 8-25 记着它是为救 novice 0.0% 胜率才加的，
「为了让人看见而加的东西，做成了全屏最看不清的一行」。

**[P2] 三个触控目标低于 44px** — #send 46x36（每轮主操作）、.chat-exit 50x31、#pickClient 99x40。
均过 SC 2.5.8 的 24px，均够不到 44px 舒适线。

**[P2] 弱焦点环还剩在别处** — .facts-more / 面板选项 / #say 仍走全局 rgba(36,61,79,.28)
（对纸底 1.66:1）。客户档案截图里「其余 3 项账户信息」外的淡框就是它，
不是布局问题（实测 .desk-body 无溢出、与页脚无重叠）。

**[P2] #home 无任何 live region** — 展开「其余 3 项账户信息」对屏幕阅读器静默。

## What's Working

1. sheet.js 是本仓最扎实的一段前端：一个实现供三处使用，dialog 语义 + 焦点陷阱 +
   Esc + 焦点归还四件不少，面板四处文字对比度 5.03–15.99 全通过。
2. 对话层三个 live region 位置都对；气泡文字 12.81:1。
3. 工作台与对话层两套视觉语言的缝接得住，换屏时 osbar 连续、动作色一致。

## Persona Red Flags

Sam（读屏/键盘）：问法面板只有一个 Tab 停靠点；#home 无 live region；.facts-more 焦点环 1.66:1。
Casey（单手手机）：发送键 36px、退出 31px；问法面板要滚才能看全七条。
Jordan（新手）：档位提示说「他」而客户是位阿姨，第一轮就会怀疑自己看错屏。

## Minor Observations

- 问法面板七个选项超出 ≤4 的工作记忆线，但七把钥匙是产品真理，不该为此砍。
  按档位重排会违反「只给词汇不给时机」，不建议做，只标出这个张力。
- 「随机一位」在换客户面板里与四位具名客户平级，而它是真实接入时唯一存在的模式。

## Questions to Consider

1. 档位提示是为了救 0.0% 胜率才加的，为什么它是全屏最看不清的一行？
2. 七种问法是这一局的全部课程，为什么键盘用户打开它只能关掉？
3. 代词管道全仓都在用，为什么最新加的那一行绕过了它——下一条新文案怎么保证不会再绕？
