import { KEYS } from './keys.js';
import { fitCanvas, paintKline, palette, roundRect } from './chart.js';
import { drawQR } from './qr.js';
import { percentileHeadline, percentileTier } from './stats.js';
import {
  SCENE, endingMeta, game, reviewKind, savedAmount, scoredTurns, wholeMoney,
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
};
export const TIER_RANK = { persuaded: 4, intercepted: 3, stalled: 2, transferred: 1 };

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
    weakBox.querySelector('.keynote').textContent = KEYS[weakest].tip;
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

/** 分享卡 = 复盘正文里"结算卡 + 三栏统计 + K 线 + 百分比"这一整块，重画一遍。
 *
 * 上一版分享卡是他说过的一句话（聊天气泡截图），现在这一块换成了复盘正文
 * 本身的内容——两者不再是两套东西：卡上有什么，正文往上翻就看得到。
 * K 线直接复用 `paintKline`，画法与屏幕上那张一模一样，不用另起一套逻辑。
 */
export function makeCard(view) {
  const W = 640;
  const pad = 40;
  const contentW = W - pad * 2;
  const c = palette();
  const kind = reviewKind();
  const meta = endingMeta(kind);
  const best = game.turns.reduce(
    (a, b) => (b.delta > (a ? a.delta : -Infinity) ? b : a), null);
  // 分享卡生成时百分位可能还没算出来（`/api/stats` 是异步旁路）：没有就不画，
  // 跟正文里 `#percentileLine` 的 hidden 处理是同一条原则，不硬凑一个数。
  const pct = typeof game._percentile === 'number' ? game._percentile : null;

  const wrap = view.querySelector('#cardWrap');
  wrap.innerHTML = '<canvas id="card"></canvas>';
  const canvas = view.querySelector('#card');

  // 版式是固定的：除了要不要那行百分比，每一块的高度都是常数，
  // 不用像上一版那样先量一句变长变短的引言才能定卡片多高。
  const TIER_TOP = pad + 34;
  const AMT_TOP = TIER_TOP + 58;
  const CAP_TOP = AMT_TOP + 54;
  const SAVED_TOP = CAP_TOP + 22;
  const DIV1 = SAVED_TOP + 34;
  const STAT_TOP = DIV1 + 26;
  const STAT_H = 74;
  const DIV2 = STAT_TOP + STAT_H + 18;
  const CHART_TOP = DIV2 + 22;
  const CHART_H = 190;
  const LEGEND_TOP = CHART_TOP + CHART_H + 14;
  const PCT_TOP = LEGEND_TOP + 30;
  const PCT_H = pct != null ? 68 : 0;
  // 卡尾那一块（2026-08-29 加）：**一张传出去的图片如果不能把人带回作品，
  // 它就只是一张图片**（就绪度审计 P1-9）。这一块就是那条路：作品名 +
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

  ctx.fillStyle = c.sub;
  ctx.font = `500 15px ${c.sans}`;
  ctx.fillText('AI 反诈劝阻', pad, pad);

  // 结局徽章：三色沿用复盘正文 .summary 的用法（劝住/拦下=品牌色，
  // 拖住=灰，转账/拉黑=红），卡片和正文不会看着像两个不同的产品
  const pill = `${meta.tier} · ${meta.title}`;
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

  ctx.fillStyle = c.text;
  ctx.font = `600 46px ${c.sans}`;
  ctx.fillText(wholeMoney(savedAmount(kind)), pad, AMT_TOP);

  ctx.fillStyle = c.note;
  ctx.font = `400 13px ${c.sans}`;
  ctx.fillText('守住的钱', pad, CAP_TOP);

  ctx.fillStyle = c.gray;
  ctx.font = `400 16px ${c.sans}`;
  ctx.fillText(meta.savedCopy, pad, SAVED_TOP);

  ctx.strokeStyle = 'rgba(0, 0, 0, 0.08)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, DIV1);
  ctx.lineTo(W - pad, DIV1);
  ctx.stroke();

  // 三栏统计，排法照抄正文的 statstrip
  const stats = [
    [String(game.trust), '最终信任度'],
    [String(game.turns.length), '用了几轮'],
    [best && best.delta > 0 ? `+${best.delta}` : '—',
      best && best.delta > 0 ? `第 ${best.round} 轮最有力` : '没有一句推动他'],
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
  const hasQR = !!url && drawQR(ctx, url, {
    x: qrX, y: FOOT_TOP, size: QR_BOX, dark: c.text, light: '#ffffff',
  });

  // 左栏与二维码垂直居中对齐。三行：一句话、作品名、地址
  const lineTop = FOOT_TOP + (QR_BOX - 74) / 2;
  ctx.fillStyle = c.text;
  ctx.font = `600 18px ${c.sans}`;
  ctx.fillText(hasQR ? '扫码，换你去劝一次' : '换你去劝一次', pad, lineTop);

  ctx.fillStyle = c.gray;
  ctx.font = `400 14px ${c.sans}`;
  ctx.fillText('AI 反诈劝阻 · 三分钟角色对调', pad, lineTop + 30);

  if (url) {
    ctx.fillStyle = c.note;
    ctx.font = `400 12px ${c.mono}`;
    // 地址一律印出来：二维码画得出来时它是给不方便扫码的人看的，
    // 画不出来时它就是唯一那条路。`shareLabel` 已经按左栏宽度截过
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
