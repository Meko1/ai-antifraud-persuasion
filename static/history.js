import { KEYS, MOODS } from './keys.js';
import { fitCanvas, loadImage, paintKline, palette, roundRect } from './chart.js';
import { contrastFacts, timingClause } from './contrast.js';
import { drawQR } from './qr.js';
import { percentileHeadline, percentileTier } from './stats.js';
import {
  SCENE, endingMeta, game, peerPronoun, reviewKind, savedAmount, scoredTurns,
  wholeMoney, withTa,
} from './state.js';

// ── 本机对局记录（跨局） ─────────────────────────────────────
//
// **标题不写"训练记录"**（8-23）：转向 C 端之后，这个人不是来受训的，
// 他是正要转账被拦下来的。他多半只打这一局——这一块因此也收进了折叠。
//
// 单局复盘（paintKeyBars 等）回答"这一局你打得怎么样"；这里回答
// "打了这么多局，你是不是在变好"。**只存本机 localStorage**——没有账号
// 体系，不识别是谁，不能跨设备合并，因此也回答不了"团队里谁最常踩合规线"
// 那类问题（那需要服务端持有用户状态，是另一件事，见交接给项目所有者的
// 待定项）。localStorage 不可用（隐私模式、被禁用）时整块不出现，
// 复盘其余部分不受影响——和 Redis 不可用时 statsWrap 的处理是同一个原则。

export const HISTORY_KEY = 'af_history_v1';
export const HISTORY_CAP = 50; // 只是个防止无限增长的上限，不是产品意图

// 结局阶梯（CONTEXT.md「结局」）：四档由高到低，被拉黑不入档，
// 因此不参与"最好成绩"这个比较——把它硬塞进排名会把"出局"读成"垫底"。
export const TIER_LABEL = {
  persuaded: '劝住', intercepted: '拦下', stalled: '拖住',
  transferred: '转账', blacklisted: '被拉黑',
  // **2026-08-29 补。** 没有这一条，下面 `TIER_LABEL[e.kind] || e.kind` 会把
  // 原始的英文键印到历史列表里——同一个状态，复盘首屏的 chip 写「未完成」，
  // 往下滚三屏变成 `unfinished`。用词与首屏保持一致。
  //
  // **只补 LABEL，不补 TIER_RANK。** 主动结束不参与「最好成绩」的比较，
  // 理由与被拉黑同一条，见上面那段注。
  unfinished: '未完成',
};
export const TIER_RANK = { persuaded: 4, intercepted: 3, stalled: 2, transferred: 1 };

/** 这一档在阶梯上站哪儿，一句话。
 *
 *  **加它是因为「拖住」这个词自己说不清是好是坏**（2026-08-31）。
 *  复盘首屏现在写着「本局结果 · 拖住 / 他说再想想 / 暂未转出，风险尚未解除」——
 *  三句都准确，可一个第一次打的人读完仍然不知道自己算打得好还是打得砸。
 *  CONTEXT.md 说结局是一道阶梯，而阶梯只画了一格出来。
 *
 *  **这不是把结局改成胜负**（POSITIONING「不做什么」拦着那一条）。
 *  胜负是给一个赢/输的判定；这里给的是位置——阶梯本来就有四档，
 *  告诉他站在第几档，是把已经存在的结构说出口，不是新加一个评价。
 *
 *  也**不是人群统计**：它不依赖任何样本，纯粹是那张阶梯表本身，
 *  所以不受「别人打成什么样」那两条样本门槛的管。
 */
export function tierRankNote(kind) {
  if (kind === 'blacklisted') return '不入档 · 这一局没走到阶梯上';
  const rank = TIER_RANK[kind];
  if (!rank) return '';                       // unfinished：主动结束，本来就没有档
  const total = Object.keys(TIER_RANK).length;
  const 从上往下 = total - rank + 1;            // 劝住=1，转账=4
  if (从上往下 === 1) return `${total} 档里最高的一档`;
  if (从上往下 === total) return `${total} 档里最低的一档`;
  const 上面 = Object.entries(TIER_RANK)
    .filter(([, r]) => r > rank)
    .sort((a, b) => a[1] - b[1])
    .map(([k]) => `「${TIER_LABEL[k]}」`);
  return `${total} 档里的第 ${从上往下} 档 · 上面还有${上面.join('、')}`;
}

export function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

/** 把这一局记一笔。每局只记一次——openReview 只在 finish() 之后触发一次，
 *  但仍然用 game 上的标记兜底，防止哪天多出一条调用路径就悄悄记重。 */
export function recordGame(kind) {
  if (game._historyRecorded) return loadHistory();
  const uses = {};
  const gains = {};
  // 进步趋势要跨局比，降级轮混进去会让某一局的"用了几次"莫名偏低
  const scored = scoredTurns();
  Object.keys(KEYS).forEach((k) => {
    const turns = scored.filter((t) => t.hits.includes(k));
    uses[k] = turns.length;
    gains[k] = turns.reduce((s, t) => s + Math.max(0, t.delta), 0);
  });
  const entry = {
    ts: Date.now(),
    sid: SCENE ? SCENE.id : '',
    clientName: SCENE ? SCENE.client.name : '',
    kind,
    trust: game.trust,
    rounds: game.turns.length,
    uses, gains,
  };
  let list = loadHistory();
  list.push(entry);
  if (list.length > HISTORY_CAP) list = list.slice(list.length - HISTORY_CAP);
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
    game._historyRecorded = true;
  } catch {
    // 存不进去（隐私模式、配额满）就当这局没记，不影响复盘其余部分
  }
  return list;
}

export function paintHistory(view, kind) {
  let list;
  try {
    list = recordGame(kind);
  } catch {
    list = null;
  }
  if (!list || !list.length) return; // historyWrap 保持 hidden，和 statsWrap 同一处理

  view.querySelector('#historyWrap').hidden = false;

  const prior = list.slice(0, -1); // 不含本局，用来算"较以往"
  const ranked = list.filter((e) => e.kind in TIER_RANK);
  const best = ranked.reduce(
    (a, b) => (TIER_RANK[b.kind] > (a ? TIER_RANK[a.kind] : -1) ? b : a), null);
  const avgTrust = prior.length
    ? Math.round(prior.reduce((s, e) => s + e.trust, 0) / prior.length)
    : null;
  const diff = avgTrust == null ? null : game.trust - avgTrust;

  const strip = view.querySelector('#historyStrip');
  strip.innerHTML = `
    <div class="stat"><b class="num">${list.length}</b><i>这台设备上打过</i></div>
    <div class="stat"><b class="num">${best ? TIER_LABEL[best.kind] : '被拉黑'}</b><i>最好成绩</i></div>
    <div class="stat"><b class="num${diff == null ? ' nil' : ''}">${
      diff == null ? '—' : (diff >= 0 ? '+' : '') + diff
    }</b><i>${diff == null ? '还没有对比' : '信任度较以往均值'}</i></div>`;

  const recentBox = view.querySelector('#historyList');
  recentBox.innerHTML = '';
  const recent = prior.slice(-5).reverse();
  if (!recent.length) {
    const p = document.createElement('p');
    p.className = 'historyempty';
    p.textContent = '这是这台设备上的第一局，下一局打完这里会有对比。';
    recentBox.appendChild(p);
  } else {
    recent.forEach((e) => {
      const row = document.createElement('div');
      row.className = 'historyrow';
      const d = new Date(e.ts);
      const when = `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')} `
        + `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
      const whenEl = document.createElement('span');
      whenEl.className = 'historywhen num';
      whenEl.textContent = when;
      const whoEl = document.createElement('span');
      whoEl.className = 'historywho';
      whoEl.textContent = e.clientName || '客户';
      const tierEl = document.createElement('span');
      tierEl.className = `historytier ${e.kind}`;
      tierEl.textContent = TIER_LABEL[e.kind] || e.kind;
      row.append(whenEl, whoEl, tierEl);
      recentBox.appendChild(row);
    });
  }

  // 只在有历史可比时才提示"最少历练"，否则这行会和本局的 keyBars 完全重复
  const weakBox = view.querySelector('#historyWeak');
  if (!prior.length) {
    weakBox.hidden = true;
  } else {
    const totalUses = {};
    Object.keys(KEYS).forEach((k) => (totalUses[k] = 0));
    list.forEach((e) => {
      Object.keys(KEYS).forEach((k) => { totalUses[k] += (e.uses && e.uses[k]) || 0; });
    });
    const weakest = Object.keys(KEYS).sort((a, b) => totalUses[a] - totalUses[b])[0];
    weakBox.hidden = false;
    weakBox.querySelector('.keyname').textContent = KEYS[weakest].name;
    weakBox.querySelector('.keynum').textContent = `${list.length} 局里用过 ${totalUses[weakest]} 次`;
    // `tip` 里带 `{ta}`，**必须过 withTa**。漏了这一层不是印错代词，是把
    // 占位符原样印在屏幕上（实测："让{ta}自己说出「这笔钱本来是给孩子办婚礼的」"）。
    // 同一份 `KEYS[k].tip` 在 review.js 的 keybars 里是包了的，这一处是同一批
    // 改动里漏掉的第二个调用点——`pronoun.test.mjs` 扫的是"有没有写死「他」"，
    // 单向，接不住这个方向的错。反向那条断言 8-31 补在同一份测试里。
    weakBox.querySelector('.keynote').textContent = withTa(KEYS[weakest].tip);
  }
}

// ── 分享卡 ──────────────────────────────────────────────────

/** 分享卡上那个二维码指向哪里。
 *
 *  **默认是当前访问地址**，所以本地、内网、目标服务器上都不用配置。
 *  `<meta name="af-share-url">` 只在一种情况下要填：作品被别人的页面
 *  代理或嵌套之后，浏览器看到的地址不是用户该扫到的那一个。
 *
 *  末尾的 `index.html` 要去掉——扫出来是个目录地址比扫出来一个文件名体面，
 *  而两者打开的是同一页。
 */
export function shareUrl() {
  const meta = typeof document !== 'undefined'
    ? document.querySelector('meta[name="af-share-url"]') : null;
  const configured = meta && meta.content ? meta.content.trim() : '';
  if (configured) return configured;
  if (typeof location === 'undefined' || !location.origin) return '';
  return `${location.origin}${location.pathname}`.replace(/index\.html$/, '');
}

/** 二维码底下那行给人读的地址。协议头对读的人没有信息量，去掉；
 *  真正编进码里的仍然是完整地址（带协议，不然扫出来不是个链接）。 */
export function shareLabel(url) {
  const short = String(url).replace(/^https?:\/\//, '').replace(/\/$/, '');
  return short.length > 42 ? `${short.slice(0, 41)}…` : short;
}

export function tierColor(kind, c) {
  if (kind === 'persuaded' || kind === 'intercepted') return c.brand;
  if (kind === 'stalled') return c.gray;
  return c.red;
}

/** 卡面主角那三行：眉、原话、那个倍数。
 *
 *  **2026-08-29 换掉了卡面主角，理由是一条外部结论。** 在此之前主角是
 *  `¥0` 与「钱一分没动，也一分没保住」——而预测一条内容会不会被转发的是
 *  **唤醒度**而不是正负（Berger & Milkman, *JMR* 2012，近 7000 篇 NYT
 *  文章）：敬畏、愤怒、焦虑会被转发，**悲伤与满足不会**。
 *  「一分没保住」正好落在低唤醒的沮丧那一格，是最不会被转发的一种。
 *
 *  而这一局手上恰好有一句高唤醒的：**同一句话，换个时候说，差 N 倍**。
 *  它既是意外，又**恰好是全作品唯一竞品没有的判据**——传播价值与技术主张
 *  第一次指向同一句话。
 *
 *  **结局没有被藏起来**：徽章还在卡头，金额退到下面那一栏统计里。
 *  换的是主角，不是事实（[POSITIONING「为拿票做设计」](../docs/POSITIONING.md)
 *  那条边界：允许为"让人看懂/愿意转发"设计，不允许越过主张边界）。
 *
 *  拿不到效力矩阵（老令牌、结局事件没下发）时返回 null，调用方退回旧版式。
 */
export function cardHero() {
  const f = contrastFacts();
  if (!f) return null;
  const named = (m) => MOODS[m] || m;

  // 倍数是这张卡的主角。`mistimed` 那一支没有"当时值多少"可比（它算的是
  // 空口断言，不走效力矩阵），所以那一支印最高档位的绝对值，不硬凑一个倍数
  const hero = f.times ? `差 ${f.times} 倍` : `最高 ${f.bestVal}×`;

  // **这张卡只在别人手机上出现**，是全作品最不容易被复核的一块——
  // 写死代词的话，六成的对局分享出去都在指错人，而分享者自己也未必回头看。
  const TA = peerPronoun();
  let note;
  // 「等…再说」还是「早几轮…的时候说」，与复盘正文共用 `timingClause`：
  // 那一档要是早就过去了，"等"字在这张卡上同样是句假话，而这张卡会被
  // 发到别人手机上，是全作品最不容易被复核的一块
  if (f.kind === 'mistimed') {
    note = `${TA}当时${named(f.mood)}，这句算空口断言；${timingClause(f)}，`
      + `是这一局分值最高的一把`;
  } else if (f.kind === 'gap') {
    note = `${TA}当时${named(f.mood)}，这一招值 ${f.val}×；`
      + `同一句话，${timingClause(f)}，值 ${f.bestVal}×`;
  } else {
    note = `同一把${f.name}，在${TA}${named(f.bestMood)}时值 ${f.bestVal}×，`
      + `在${TA}${named(f.worstMood)}时只值 ${f.worstVal}×`;
  }

  return {
    eyebrow: f.round ? `第 ${f.round} 轮 · ${f.name}` : `同一句话，换个时候说 · ${f.name}`,
    quote: f.quote,
    hero,
    note,
  };
}

/** 按给定宽度断行。canvas 没有自动换行，玩家那句原话最长 120 字，得自己折。
 *
 *  **按字符逐个量，不按标点或空格断**：这一句大概率是中文，中文没有词边界，
 *  按空格断会得到一整行不折。返回的行数由调用方截断——卡上只留得下两行。
 */
export function wrapText(ctx, text, maxWidth, maxLines) {
  const lines = [];
  let line = '';
  for (const ch of String(text)) {
    if (line && ctx.measureText(line + ch).width > maxWidth) {
      lines.push(line);
      line = ch;
      if (lines.length === maxLines) {
        // 装满了还有字没放完：末行让出一个字的位置给省略号，让"被截断了"
        // 这件事看得出来。不省略地硬切，读的人会以为原话就是那么短
        lines[maxLines - 1] = `${lines[maxLines - 1].slice(0, -1)}…`;
        return lines;
      }
    } else {
      line += ch;
    }
  }
  if (line) lines.push(line);
  return lines;
}

/** 分享卡 = 复盘正文里"结算卡 + 三栏统计 + K 线 + 百分比"这一整块，重画一遍。
 *
 * 上一版分享卡是他说过的一句话（聊天气泡截图），现在这一块换成了复盘正文
 * 本身的内容——两者不再是两套东西：卡上有什么，正文往上翻就看得到。
 * K 线直接复用 `paintKline`，画法与屏幕上那张一模一样，不用另起一套逻辑。
 *
 * **2026-09-05 重排版式，理由不是"差 N 倍"这个钩子错了——它是 2026-08-29
 * 按 Berger & Milkman（转发靠唤醒度不靠好坏）刻意选的，CONTEST.md §6.3 S1
 * 有案可查。理由是这张卡此前假设看的人已经懂这个作品**：卡上没有一个字说
 * 这是什么、和谁有关，只有一堆对老玩家才有意义的档位名与倍数。而分享卡的
 * 真实观众恰恰是最不懂这些的那批人——CONTEST.md §6.2 那张漏斗图写得很清楚，
 * 转发出去的每一张卡都在造"新观众"，不是发给已经打过的人自己看。
 *
 * 三处改动：
 *   1. 加妙想品牌条（顶部与卡尾各一次）——陌生人第一件事是确认"这是什么，
 *      谁在做"，此前这张卡一处品牌痕迹都没有。
 *   2. 卡面主角换成结局标题本身（`meta.title`，如"她还是转走了"）——
 *      一句人话，不需要先懂这个作品的任何机制就能看懂，而且天然是叙事性的
 *      高唤醒内容，不比一个抽象倍数弱。
 *   3. "差 N 倍"降级成主角下方一块有边框的"洞察卡"，内容一个字没删——
 *      仍然是这个作品唯一竞品没有的判据，只是不再要求陌生人一上来就看懂它。
 *
 * `cardHero()` 的返回契约没有变（仍然是"拿不到效力矩阵就 null"），
 * 变的只是 `makeCard` 怎么用它：主角不再依赖 `hero`，`hero` 只决定
 * 要不要画那块洞察卡。
 */
export async function makeCard(view) {
  const W = 640;
  const pad = 40;
  const contentW = W - pad * 2;
  const c = palette();
  const kind = reviewKind();
  const meta = endingMeta(kind);
  // 分享卡生成时百分位可能还没算出来（`/api/stats` 是异步旁路）：没有就不画，
  // 跟正文里 `#percentileLine` 的 hidden 处理是同一条原则，不硬凑一个数。
  const pct = typeof game._percentile === 'number' ? game._percentile : null;

  // 妙想小图标（真实资产，`static/assets/miaoxiang-mark.png`，与入口卡、
  // 拦截交接屏同一份文件）。`loadImage` 从不 reject——加载失败也不该让
  // 一张分享卡因为一个图标生成不出来，那时只是少画这一笔，"妙想 · 账户
  // 安全"那半句文字照常画，品牌关联不能全押在一张图标能不能加载上。
  const mark = await loadImage('static/assets/miaoxiang-mark.png');

  const wrap = view.querySelector('#cardWrap');
  wrap.innerHTML = '<canvas id="card"></canvas>';
  const canvas = view.querySelector('#card');

  // 卡面配角：`cardHero()` 给的那三行（差 N 倍那块洞察卡的内容）。
  // 拿不到效力矩阵时是 null——此前整张卡的版式都押在它上面，现在只影响
  // 要不要画那一块，主角（结局标题）不依赖它。
  const hero = cardHero();

  // 版式仍然是固定的：主角标题、玩家原话、洞察卡里的说明，三处都可能
  // 换行，不先量一遍就定不了卡片多高。
  const probe = fitCanvas(document.createElement('canvas'), 1, 1);
  probe.font = `700 28px ${c.sans}`;
  const headlineLines = wrapText(probe, meta.title, contentW, 2);
  probe.font = `400 17px ${c.sans}`;
  const quoteLines = hero && hero.quote
    ? wrapText(probe, `「${hero.quote}」`, contentW - 14, 2) : [];
  probe.font = `400 13px ${c.sans}`;
  const noteLines = hero ? wrapText(probe, hero.note, contentW - 28, 3) : [];

  const BRAND_TOP = pad;
  const TIER_TOP = BRAND_TOP + 22 + 16;
  const HEADLINE_TOP = TIER_TOP + 44;
  const HEADLINE_LINE_H = 36;
  const HEADLINE_H = headlineLines.length * HEADLINE_LINE_H;
  const CONCEPT_TOP = HEADLINE_TOP + HEADLINE_H + 6;
  const QUOTE_TOP = CONCEPT_TOP + 30;
  const QUOTE_H = quoteLines.length ? quoteLines.length * 26 + 12 : 0;
  // 洞察卡（"差 N 倍"降级之后的落点）：一块带底色的圆角矩形，
  // 内容自上而下是——第几轮用了哪把钥匙（小字）／差 N 倍（加粗）／
  // 完整说明（小字，可能两三行）。没有 hero 时高度为 0，整块不画。
  const BOX_TOP = QUOTE_TOP + QUOTE_H + (quoteLines.length ? 10 : 0);
  const BOX_PAD = 16;
  const BOX_H = hero
    ? BOX_PAD * 2 + 18 + 32 + Math.max(1, noteLines.length) * 19
    : 0;
  const AFTER_HERO = hero ? BOX_TOP + BOX_H : CONCEPT_TOP + 30;
  const DIV1 = AFTER_HERO + 22;
  const STAT_TOP = DIV1 + 26;
  const STAT_H = 74;
  const DIV2 = STAT_TOP + STAT_H + 18;
  const CHART_TOP = DIV2 + 22;
  const CHART_H = 190;
  const LEGEND_TOP = CHART_TOP + CHART_H + 14;
  const PCT_TOP = LEGEND_TOP + 30;
  const PCT_H = pct != null ? 68 : 0;
  // 卡尾那一块（2026-08-29 加）：**一张传出去的图片如果不能把人带回作品，
  // 它就只是一张图片**（就绪度审计 P1-9）。这一块就是那条路：品牌 + 作品名 +
  // 一句让人想扫的话 + 二维码 + 给人读的地址。
  //
  // 它属于「让人看懂」，不属于「让人多打几局」——POSITIONING「不做什么」
  // 那条线画在这里：加二维码是前者，加连胜奖励是后者。
  const DIV3 = PCT_TOP + PCT_H + (pct != null ? 20 : -4);
  const FOOT_TOP = DIV3 + 20;
  const QR_BOX = 116;
  const H = FOOT_TOP + QR_BOX + 10;

  const ctx = fitCanvas(canvas, W, H);

  // 底色用聊天背景的灰：整张卡就该看着像一张微信截图，而不像一张海报
  ctx.fillStyle = c.bg;
  ctx.fillRect(0, 0, W, H);
  ctx.textBaseline = 'top';
  ctx.textAlign = 'left';

  // 品牌条：图标 + 「妙想 · 账户安全」——这是这张卡此前完全没有的东西。
  // 分享卡是"新观众"唯一会看到的一屏（CONTEST.md §6.2 的漏斗图），
  // 陌生人第一件事是确认"这是什么、谁在做"，而不是先看懂一个游戏机制。
  // 「账户安全」不是随手写的——CONTEST.md 创意思路末句就是「设计为妙想的
  // 账户安全技能」，这里原样借用同一个措辞，不新造一个说法。
  ctx.textBaseline = 'middle';
  if (mark) ctx.drawImage(mark, pad, BRAND_TOP + 1, 20, 20);
  ctx.fillStyle = c.text;
  ctx.font = `600 14px ${c.sans}`;
  ctx.fillText('妙想 · 账户安全', pad + (mark ? 28 : 0), BRAND_TOP + 11);
  // 产品名仍在卡上（CONTEST.md：分享卡水印是产品名固定出现的三处之一），
  // 只是从"标题"退成右上角的署名——品牌那半句才是陌生人该先读到的
  ctx.fillStyle = c.sub;
  ctx.font = `500 13px ${c.sans}`;
  ctx.textAlign = 'right';
  ctx.fillText('AI 反诈劝阻', W - pad, BRAND_TOP + 11);
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';

  // 结局徽章：三色沿用复盘正文 .summary 的用法（劝住/拦下=品牌色，
  // 拖住=灰，转账/拉黑=红），卡片和正文不会看着像两个不同的产品
  const pill = `${meta.tier}`;
  ctx.font = `600 15px ${c.sans}`;
  const pillW = ctx.measureText(pill).width + 24;
  ctx.fillStyle = tierColor(kind, c);
  ctx.globalAlpha = 0.12;
  roundRect(ctx, pad, TIER_TOP, pillW, 30, 15);
  ctx.fill();
  ctx.globalAlpha = 1;
  ctx.fillStyle = tierColor(kind, c);
  ctx.textBaseline = 'middle';
  ctx.fillText(pill, pad + 12, TIER_TOP + 16);
  ctx.textBaseline = 'top';

  // 卡面主角：结局标题本身，一句人话，不需要先懂任何机制。
  // 颜色跟徽章同一套（tierColor），好坏在一眼的色块上已经写明白了。
  ctx.fillStyle = tierColor(kind, c);
  ctx.font = `700 28px ${c.sans}`;
  headlineLines.forEach((line, i) => ctx.fillText(line, pad, HEADLINE_TOP + i * HEADLINE_LINE_H));

  // 一句概念说明——补的正是这张卡此前缺的那句话：这到底是什么。
  // 原样借用入口卡 / meta description 已经验证过的钩子，不新写一套说法。
  ctx.fillStyle = c.gray;
  ctx.font = `400 14px ${c.sans}`;
  ctx.fillText(withTa('十轮角色对调，你能不能劝住正在被骗的{ta}'), pad, CONCEPT_TOP);

  if (hero) {
    // 玩家自己那句原话。**它是这张卡最像"截图"的一处**——人转发的是
    // 一句话，不是一份成绩（HANDOFF「分享卡是一张聊天截图」那条原则还在）
    if (quoteLines.length) {
      ctx.fillStyle = tierColor('stalled', c);
      ctx.fillRect(pad, QUOTE_TOP + 2, 3, quoteLines.length * 26 - 6);
      ctx.fillStyle = c.text;
      ctx.font = `400 17px ${c.sans}`;
      quoteLines.forEach((line, i) => ctx.fillText(line, pad + 14, QUOTE_TOP + i * 26));
    }

    // 洞察卡："差 N 倍"降级之后的落点——内容一个字没少，只是不再是
    // 46px 的巨大数字，而是一块带边框的说明区，陌生人可以跳过它，
    // 玩过的人细读能读懂"时机"这条判据到底在说什么。
    ctx.fillStyle = c.bg === c.white ? c.line : 'rgba(0, 0, 0, 0.035)';
    roundRect(ctx, pad, BOX_TOP, contentW, BOX_H, 12);
    ctx.fill();
    ctx.strokeStyle = c.goalText;
    ctx.globalAlpha = 0.35;
    ctx.lineWidth = 1;
    roundRect(ctx, pad + 0.5, BOX_TOP + 0.5, contentW - 1, BOX_H - 1, 12);
    ctx.stroke();
    ctx.globalAlpha = 1;

    ctx.fillStyle = c.note;
    ctx.font = `500 12px ${c.sans}`;
    ctx.fillText(hero.eyebrow, pad + BOX_PAD, BOX_TOP + BOX_PAD);

    ctx.fillStyle = c.goalText;
    ctx.font = `700 24px ${c.sans}`;
    ctx.fillText(hero.hero, pad + BOX_PAD, BOX_TOP + BOX_PAD + 20);

    ctx.fillStyle = c.gray;
    ctx.font = `400 13px ${c.sans}`;
    noteLines.forEach((line, i) => ctx.fillText(
      line, pad + BOX_PAD, BOX_TOP + BOX_PAD + 52 + i * 19,
    ));
  }

  ctx.strokeStyle = 'rgba(0, 0, 0, 0.08)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, DIV1);
  ctx.lineTo(W - pad, DIV1);
  ctx.stroke();

  // 三栏统计，排法照抄正文的 statstrip。**不再按 hero 分两套**——
  // 主角换成结局标题之后，这三个数对任何一局都是同样有意义的三件事：
  // 守住多少钱、最后信任度多少、打了几轮，不需要再分"有没有效力矩阵"。
  const stats = [
    [wholeMoney(savedAmount(kind)), '守住的钱'],
    [String(game.trust), '最终信任度'],
    [String(game.turns.length), '用了几轮'],
  ];
  const colW = contentW / 3;
  ctx.textAlign = 'center';
  stats.forEach(([num, label], i) => {
    const cx = pad + colW * i;
    if (i) {
      ctx.strokeStyle = 'rgba(0, 0, 0, 0.08)';
      ctx.beginPath();
      ctx.moveTo(cx, STAT_TOP - 4);
      ctx.lineTo(cx, STAT_TOP + STAT_H - 14);
      ctx.stroke();
    }
    ctx.fillStyle = c.text;
    ctx.font = `600 24px ${c.sans}`;
    ctx.fillText(num, cx + colW / 2, STAT_TOP);
    ctx.fillStyle = c.note;
    ctx.font = `400 12px ${c.sans}`;
    ctx.fillText(label, cx + colW / 2, STAT_TOP + 32);
  });
  ctx.textAlign = 'left';

  ctx.strokeStyle = 'rgba(0, 0, 0, 0.08)';
  ctx.beginPath();
  ctx.moveTo(pad, DIV2);
  ctx.lineTo(W - pad, DIV2);
  ctx.stroke();

  // K 线：跟正文用的是同一个画法，复盘里看到的是什么，卡上就是什么
  paintKline(ctx, { x: pad, y: CHART_TOP, w: contentW, h: CHART_H }, {
    turns: game.turns,
    threshold: game.threshold,
    slots: Math.max(game.turns.length, 6),
    palette: c,
    axis: true,
    start: game.turns.length ? game.turns[0].before : null,
  });

  ctx.fillStyle = c.note;
  ctx.font = `400 12px ${c.sans}`;
  ctx.fillText('一根蜡烛一轮，红涨绿跌', pad, LEGEND_TOP);

  // 百分比是这张卡在复盘正文里也常驻显示的东西——配色跟正文的
  // percentileTier 是同一条规则：够亮眼才给品牌绿，其余中性灰，
  // 不是每次都用"值得庆祝"那罐颜色。文案用简短版（percentileHeadline），
  // 卡片宽度有限，正文那句带具体建议的长版放不下一行
  if (pct != null) {
    const good = percentileTier(pct) === 'good';
    ctx.fillStyle = good ? c.brand : c.line;
    ctx.globalAlpha = good ? 0.1 : 1;
    roundRect(ctx, pad, PCT_TOP, contentW, PCT_H, 10);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = good ? c.brand : c.gray;
    ctx.font = `600 20px ${c.sans}`;
    ctx.textBaseline = 'middle';
    ctx.fillText(`信任度${percentileHeadline(pct)}`, pad + 18, PCT_TOP + PCT_H / 2);
    ctx.textBaseline = 'top';
  }

  ctx.strokeStyle = 'rgba(0, 0, 0, 0.08)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, DIV3);
  ctx.lineTo(W - pad, DIV3);
  ctx.stroke();

  // 二维码先画：它画不出来（地址太长，实际上碰不到）时左边那几行要改写法，
  // 所以得先知道结果
  const url = shareUrl();
  const qrX = W - pad - QR_BOX;

  // 二维码外框（2026-09-11 加）：借的是大赛平台自己「扫码分享」卡片的
  // 做法——深色渐变光晕框，让码本身更像"要被伸手去扫的东西"，不是版面
  // 角落一块随手贴的方块。**只镶框，不动底色**：这张卡的地基论点仍然是
  // "看着像一张微信截图"（见 `makeCard` 顶部那段长注释），框是这张卡上
  // 唯一允许"看起来像海报"的地方——它挨着的正是最需要被一眼认出来的
  // 那个元素，不会稀释"这是一张聊天截图"这条整体判断。
  // 渐变两端取 --mx-violet / --mx-brand，与桌面舞台、第一轮揭晓用的是
  // 同一份 token（见 static/style.css），不给这张卡另开一套颜色。
  if (url) {
    const FRAME_PAD = 10;
    const frameX = qrX - FRAME_PAD;
    const frameY = FOOT_TOP - FRAME_PAD;
    const frameSize = QR_BOX + FRAME_PAD * 2;
    ctx.save();
    ctx.shadowColor = 'rgba(43, 92, 230, .38)';
    ctx.shadowBlur = 20;
    ctx.fillStyle = '#0d0f2b';
    roundRect(ctx, frameX, frameY, frameSize, frameSize, 18);
    ctx.fill();
    ctx.restore();
    const frameGrad = ctx.createLinearGradient(
      frameX, frameY, frameX + frameSize, frameY + frameSize);
    frameGrad.addColorStop(0, '#7b3fe4');
    frameGrad.addColorStop(1, '#1e8cf0');
    ctx.strokeStyle = frameGrad;
    ctx.lineWidth = 1.5;
    roundRect(ctx, frameX + 0.75, frameY + 0.75, frameSize - 1.5, frameSize - 1.5, 17);
    ctx.stroke();
  }

  const hasQR = !!url && drawQR(ctx, url, {
    x: qrX, y: FOOT_TOP, size: QR_BOX, dark: c.text, light: '#ffffff',
  });

  // 左栏与二维码垂直居中对齐。**这里多加了一行**（品牌条），块高跟着 +26。
  //
  // **2026-08-30：二维码画得出来时不再印地址。** 原先一律印，理由是
  // "画得出来时它是给不方便扫码的人看的"——而作品上线到大赛平台之后，
  // 那条地址去掉协议头有 61 个字符，`shareLabel` 的 42 字上限正好**截在
  // 唯一标识之前**（`…/ai-creator-…`），照着敲也敲不出来。一条打不开的地址
  // 不是备用路径，是噪音。
  //
  // **但二维码画不出来时它仍然是唯一那条路**，那一支照旧印，不许一起删掉。
  const lines = hasQR ? 2 : 3;
  const blockH = (lines === 2 ? 48 : 74) + 26;
  const lineTop = FOOT_TOP + (QR_BOX - blockH) / 2 + 26;

  // 卡尾再露一次品牌——扫码/CTA 这个"要不要行动"的瞬间，比开头那一眼
  // 更值得把"这是妙想的技能"钉一遍
  if (mark) ctx.drawImage(mark, pad, lineTop - 26, 14, 14);
  ctx.fillStyle = c.note;
  ctx.font = `500 12px ${c.sans}`;
  ctx.fillText('妙想 · 账户安全', pad + (mark ? 18 : 0), lineTop - 25);

  ctx.fillStyle = c.text;
  ctx.font = `600 18px ${c.sans}`;
  ctx.fillText(hasQR ? '扫码，换你去劝一次' : '换你去劝一次', pad, lineTop);

  ctx.fillStyle = c.gray;
  ctx.font = `400 14px ${c.sans}`;
  // **口径跟着界面走**（2026-09-05 从 12 改到 10，随 `MAX_ROUNDS` 一起）：
  // 这一行是**被截图传出去的那一行**——`index.html` 的交接屏与这里若不一致，
  // 外面看到的是这一行。轮次是这个作品说了算的量，分钟不是。
  ctx.fillText('AI 反诈劝阻 · 十轮角色对调', pad, lineTop + 30);

  if (url && !hasQR) {
    ctx.fillStyle = c.note;
    ctx.font = `400 12px ${c.mono}`;
    ctx.fillText(shareLabel(url), pad, lineTop + 56);
  }

  canvas.toBlob((blob) => {
    if (!blob) return;
    const p = document.createElement('p');
    p.className = 'savehint';
    const a = document.createElement('a');
    a.className = 'savelink';
    a.href = URL.createObjectURL(blob);
    a.download = `反诈劝阻-${meta.tier}.png`;
    a.textContent = '保存图片';
    p.append(a, document.createTextNode('，手机上也可以长按上图保存'));
    wrap.appendChild(p);
  }, 'image/png');
}
