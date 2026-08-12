/* AI 反诈劝阻 · 前端
 *
 * 服务端不存会话：每轮请求都要带上一轮返回的 token（ADR-0003）。
 * SSE 事件顺序恒为 meta → sentence* → score → ending? → state → done，
 * score 刻意排在台词播完之后——信任度那一跳要等他把话说完才发生。
 *
 * 界面语法照搬微信：判分卡走「系统提示」的位置，对局状态走「群公告」的位置。
 */

'use strict';

// ── 闭集与知识点 ────────────────────────────────────────────
// 标识与 app/scoring.py 的 KEY_VALUES / PENALTY_VALUES 一一对应。
// 分值不抄过来：那是判分引擎的事，前端只认服务端下发的 delta。

const KEYS = {
  anchor_real_purpose: {
    name: '锚定真实用途',
    tip: '被洗脑的人满脑子是收益率。让他自己说出「这笔钱本来是给孩子办婚礼的」，' +
         '抽象的数字才会变回具体的代价——一线劝阻里最有效的一招，也最难开口问。',
  },
  socratic_question: {
    name: '苏格拉底式提问',
    tip: '直接否定只会激起防御。让他自己去按那个提现键、自己去看账户开户名是谁，' +
         '结论由他自己得出，才立得住。',
  },
  expose_contradiction: {
    name: '指出内部矛盾',
    tip: '骗局的话术经不起并置：「内部消息」和「几百人的群」不可能同时成立。' +
         '用他自己说过的话去顶，比引用任何新闻都管用。',
  },
};

const PENALTIES = {
  scold: { name: '责骂', tip: '一旦被骂，他就把你归进「不理解我的人」，从此不再开口。' },
  preach: { name: '说教', tip: '摆数据像上课。他要的不是知识，是一个能下的台阶。' },
  bare_assertion: { name: '空口断言', tip: '「这是诈骗」四个字他这三个月听了无数遍，早免疫了。' },
};

const ENDINGS = {
  persuaded: { title: '他把钱留住了', sub: '三十万还在卡里' },
  blacklisted: { title: '他把你拉黑了', sub: '消息再也发不出去' },
  transferred: { title: '他还是转走了', sub: '三点整，三十万到账' },
};

// 转账金额与收款方在这儿写死：它们是剧本设定（app/gateway.py 的 SCAM_SCRIPT），
// 不是判分参数，服务端也不下发。
const AMOUNT = '¥300,000.00';
const PAYEE = '转账给 启航投顾-李';

const ERRORS = {
  invalid_state: '这一局放得太久了，得重开一局',
  upstream_unavailable: '消息没发出去，再说一次试试',
  rate_limited: '这会儿人有点多，等两秒再说',
  internal: '出了点岔子，再说一次试试',
  network: '网络断了，这句没发出去',
};

const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ── 状态 ────────────────────────────────────────────────────

const game = {
  token: '',
  contestId: '',
  opening: '',
  trust: 0,
  threshold: 80,
  maxRounds: 12,
  remaining: 12,
  turns: [],      // {round, utterance, reply, hits, grounded, delta, trust, before, pool}
  ending: null,   // {kind, trust, line}
  busy: false,
  entered: false,
};

const $ = (id) => document.getElementById(id);
const thread = $('thread');

// ── SSE over POST ───────────────────────────────────────────
// EventSource 只能发 GET，而每轮要把令牌 POST 上去，因此自己解一遍协议。

async function* readEvents(resp) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let cut;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let name = null;
      let data = '';
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (name) yield { name, data: data ? JSON.parse(data) : {} };
    }
  }
}

// ── K 线（复盘与分享卡共用）──────────────────────────────────
// 一根蜡烛就是一轮：开＝上轮信任度，收＝本轮信任度，细横线＝这一轮判分给出的
// 分数。横线与实体端点的落差，正是每轮的信任流失与蓄势池的存取。

function paintKline(ctx, box, opts) {
  const { x, y, w, h } = box;
  const { turns, threshold, slots, palette: c } = opts;
  const py = (v) => y + h - (Math.max(0, Math.min(100, v)) / 100) * h;

  ctx.save();

  ctx.strokeStyle = c.goal;
  ctx.setLineDash([3, 4]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, py(threshold) + 0.5);
  ctx.lineTo(x + w, py(threshold) + 0.5);
  ctx.stroke();
  ctx.setLineDash([]);

  if (opts.axis) {
    ctx.fillStyle = c.goal;
    ctx.font = `500 ${opts.labelSize || 10}px ${c.sans}`;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'bottom';
    ctx.fillText(`劝住 ${threshold}`, x + 2, py(threshold) - 3);
  }

  const count = Math.max(slots, turns.length, 1);
  const step = w / count;
  const width = Math.max(3, Math.min(opts.maxWidth || 18, step * 0.56));

  turns.forEach((t, i) => {
    const cx = x + step * (i + 0.5);
    const open = t.before;
    const close = t.trust;
    const raw = Math.max(0, Math.min(100, open + t.delta));
    const color = close > open ? c.rise : close < open ? c.fall : c.sub;

    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.5;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(cx) + 0.5, py(Math.max(open, close, raw)));
    ctx.lineTo(Math.round(cx) + 0.5, py(Math.min(open, close, raw)));
    ctx.stroke();
    ctx.globalAlpha = 1;

    const top = py(Math.max(open, close));
    const bottom = py(Math.min(open, close));
    ctx.fillStyle = color;
    ctx.fillRect(cx - width / 2, top, width, Math.max(2, bottom - top));

    // 判分线：只画影线的话，蓄势池「释放」那一侧会看不见
    ctx.strokeStyle = c.text;
    ctx.globalAlpha = 0.45;
    ctx.beginPath();
    ctx.moveTo(cx - width * 0.72, Math.round(py(raw)) + 0.5);
    ctx.lineTo(cx + width * 0.72, Math.round(py(raw)) + 0.5);
    ctx.stroke();
    ctx.globalAlpha = 1;
  });

  ctx.strokeStyle = c.line;
  ctx.lineWidth = 1;
  for (let i = turns.length; i < count; i++) {
    const cx = x + step * (i + 0.5);
    ctx.beginPath();
    ctx.moveTo(cx - width / 2, y + h - 0.5);
    ctx.lineTo(cx + width / 2, y + h - 0.5);
    ctx.stroke();
  }

  ctx.restore();
}

function palette() {
  const s = getComputedStyle(document.documentElement);
  const v = (n) => s.getPropertyValue(n).trim();
  return {
    rise: v('--wx-rise'), fall: v('--wx-fall'), brand: v('--wx-brand'),
    sub: v('--wx-sub'), line: v('--wx-line'), text: v('--wx-text'),
    white: v('--wx-white'), gray: v('--wx-gray'), goal: '#c9a227',
    sans: '-apple-system, "PingFang SC", "Microsoft YaHei", sans-serif',
    mono: 'ui-monospace, Menlo, Consolas, monospace',
  };
}

function fitCanvas(canvas, cssW, cssH) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  return ctx;
}

// ── 对话 ────────────────────────────────────────────────────

function toBottom() {
  thread.scrollTop = thread.scrollHeight;
}

function avatar(who) {
  const el = document.createElement('span');
  el.className = 'avatar' + (who === 'them' ? ' av-chen' : '');
  el.setAttribute('aria-hidden', 'true');
  el.textContent = who === 'them' ? '陈' : '我';
  return el;
}

function say(who, text) {
  const row = document.createElement('div');
  row.className = `msg ${who}`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  row.append(avatar(who), bubble);
  thread.appendChild(row);
  toBottom();
  return bubble;
}

function divider(text) {
  const el = document.createElement('div');
  el.className = 'timedivider';
  el.textContent = text;
  thread.appendChild(el);
}

/** 系统提示。微信用它显示「你撤回了一条消息」，判分卡走同一个位置。 */
function sysnote(nodes, bad) {
  const el = document.createElement('div');
  el.className = 'sysnote' + (bad ? ' bad' : '');
  nodes.forEach((n, i) => {
    if (i) {
      const sep = document.createElement('span');
      sep.className = 'sep';
      sep.textContent = '·';
      el.appendChild(sep);
    }
    el.appendChild(typeof n === 'string' ? document.createTextNode(n) : n);
  });
  thread.appendChild(el);
  toBottom();
  return el;
}

function tag(id) {
  const meta = KEYS[id] || PENALTIES[id];
  const el = document.createElement('span');
  el.className = 'tag ' + (KEYS[id] ? 'key' : 'penalty');
  el.textContent = meta ? meta.name : id;
  return el;
}

function hitTags(hits, grounded) {
  const out = hits.map(tag);
  if (hits.some((h) => KEYS[h])) {
    const g = document.createElement('span');
    g.className = 'tag';
    g.textContent = grounded ? '扎根' : '未扎根，效力打折';
    out.push(g);
  } else if (!hits.length) {
    const n = document.createElement('span');
    n.className = 'tag';
    n.textContent = '没使上劲';
    out.push(n);
  }
  return out;
}

function showVerdict(score) {
  const d = document.createElement('b');
  d.className = 'delta num ' + (score.delta > 0 ? 'up' : score.delta < 0 ? 'down' : 'flat');
  d.textContent = score.delta > 0 ? `+${score.delta}` : String(score.delta);
  sysnote([d, ...hitTags(score.hits, score.grounded)]);
}

function paintTrust() {
  const pct = Math.max(0, Math.min(100, game.trust));
  const fill = $('trustFill');
  fill.style.width = pct + '%';
  fill.className = 'st-fill' + (pct < 20 ? ' danger' : pct < 45 ? ' low' : '');
  $('trustGoal').style.left = game.threshold + '%';
}

function countTo(el, from, to, ms) {
  el.dataset.target = String(to);
  if (REDUCED || from === to || document.hidden) {
    el.textContent = String(to);
    return;
  }
  const started = performance.now();
  const step = (now) => {
    const p = Math.min(1, (now - started) / ms);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = String(Math.round(from + (to - from) * eased));
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  // 兜底：玩家切出去看一眼别的，rAF 就停摆了。动画可以丢，数值不能丢。
  setTimeout(() => {
    if (el.dataset.target === String(to)) el.textContent = String(to);
  }, ms + 80);
}

function bumpTrust(next, delta) {
  countTo($('trust'), game.trust, next, 520);
  game.trust = next;
  paintTrust();
  flashDelta(delta);
}

/** 涨跌值在信任度旁边亮一下就走。状态条上只留结果，过程留在判分卡里。 */
function flashDelta(delta) {
  const el = $('trustDelta');
  if (!delta) {
    el.className = 'st-delta num';
    return;
  }
  el.textContent = delta > 0 ? `+${delta}` : String(delta);
  el.className = 'st-delta num show ' + (delta > 0 ? 'up' : 'down');
  clearTimeout(flashDelta.timer);
  flashDelta.timer = setTimeout(() => el.classList.remove('show'), 2600);
}

// ── 一轮 ────────────────────────────────────────────────────

async function playTurn(utterance) {
  game.busy = true;
  $('send').disabled = true;
  $('say').disabled = true;

  say('me', utterance);
  const bubble = say('them', '');
  bubble.classList.add('typing');

  let resp;
  try {
    resp = await fetch('/api/game/turn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: game.token, utterance }),
    });
  } catch (e) {
    bubble.parentElement.remove();
    return failTurn('network');
  }

  if (!resp.ok || !resp.body) {
    bubble.parentElement.remove();
    return failTurn('internal');
  }

  let round = null;
  let spoken = '';
  let score = null;
  let failed = null;

  try {
    for await (const ev of readEvents(resp)) {
      if (ev.name === 'meta') {
        round = ev.data.round;
        game.remaining = ev.data.remaining;
        $('remaining').textContent = String(ev.data.remaining);
      } else if (ev.name === 'sentence') {
        spoken += ev.data.text;
        bubble.textContent = spoken;
        toBottom();
      } else if (ev.name === 'score') {
        score = ev.data;
      } else if (ev.name === 'ending') {
        game.ending = ev.data;
      } else if (ev.name === 'state') {
        game.token = ev.data.token;
      } else if (ev.name === 'error') {
        failed = ev.data.code;
      }
    }
  } catch (e) {
    failed = failed || 'network';
  }

  bubble.classList.remove('typing');
  if (!spoken) bubble.parentElement.remove();

  if (failed) return failTurn(failed);

  if (score) {
    game.turns.push({
      round: round || game.turns.length + 1,
      utterance,
      reply: spoken,
      hits: score.hits,
      grounded: score.grounded,
      delta: score.delta,
      trust: score.trust,
      before: game.trust,
      pool: score.pool,
    });
    bumpTrust(score.trust, score.delta);
    showVerdict(score);
    if (score.degraded) sysnote(['这一轮没判出标签，按不加不减处理']);
  }

  if (game.ending) return finish();

  game.busy = false;
  $('send').disabled = false;
  $('say').disabled = false;
  $('say').focus();
}

function failTurn(code) {
  sysnote([ERRORS[code] || ERRORS.internal], true);
  game.busy = false;

  if (code === 'invalid_state') {
    $('composer').hidden = true;
    const again = document.createElement('button');
    again.textContent = '重开一局';
    again.onclick = () => location.reload();
    sysnote(['这一局放得太久了', again], true);
    return;
  }
  $('send').disabled = false;
  $('say').disabled = false;
  $('say').focus();
}

/** 转账凭证。他截了张图发过来——微信里人人都干过这事。
 *
 * 劝住与转走共用这一张卡，只翻一个位：橙的是已被接收，灰的是没点下去。
 * 整局游戏赌的就是这一位，所以它值得是这一屏上唯一一件重物。
 */
function receipt(cancelled) {
  const el = document.createElement('div');
  el.className = 'receipt' + (cancelled ? ' void' : '');
  el.innerHTML =
    '<div class="receipt-body">' +
      '<div class="receipt-icon" aria-hidden="true">¥</div>' +
      '<div><div class="receipt-amt"></div><div class="receipt-to"></div></div>' +
    '</div><div class="receipt-foot"></div>';
  el.querySelector('.receipt-amt').textContent = AMOUNT;
  el.querySelector('.receipt-to').textContent = PAYEE;
  el.querySelector('.receipt-foot').textContent =
    cancelled ? '已取消转账' : '15:00 已被对方接收';
  el.setAttribute('role', 'img');
  el.setAttribute('aria-label',
    `转账凭证截图：${AMOUNT}，${PAYEE}，` + (cancelled ? '已取消转账' : '已被对方接收'));
  return el;
}

/** 图片消息：没有底色、没有尖角，气泡只是个容器 */
function photo(node) {
  const row = document.createElement('div');
  row.className = 'msg them';
  const bubble = document.createElement('div');
  bubble.className = 'bubble photo';
  bubble.appendChild(node);
  row.append(avatar('them'), bubble);
  thread.appendChild(row);
  toBottom();
}

function finish() {
  const kind = game.ending.kind;
  if (game.ending.line) say('them', game.ending.line);
  $('composer').hidden = true;

  // 结局不另起一块 UI，它就是这段对话里的最后一件东西。
  if (kind === 'blacklisted') {
    // 被拉黑之后微信显示的就是这张卡。开局那句「对方不是你的朋友」在这里合上了口：
    // 你一直是个陌生人，现在连话都递不进去了。
    const tip = document.createElement('p');
    tip.className = 'strangertip';
    tip.textContent = '对方开启了朋友验证，你还不是他（她）朋友。';
    thread.appendChild(tip);
  } else {
    photo(receipt(kind === 'persuaded'));
  }

  const btn = document.createElement('button');
  btn.className = 'endcta';
  btn.type = 'button';
  btn.textContent = '看复盘';
  btn.onclick = openReview;
  thread.appendChild(btn);
  toBottom();
  btn.focus({ preventScroll: true });
}

// ── 复盘 ────────────────────────────────────────────────────

/** 用逐轮数据说一句具体的话。失败的结局也要能说出玩家做对了什么。 */
function verdictCopy() {
  const kind = game.ending ? game.ending.kind : 'transferred';
  const best = game.turns.reduce(
    (a, b) => (b.delta > (a ? a.delta : -Infinity) ? b : a), null);
  const parts = [];

  if (kind === 'persuaded') parts.push(`你用了 ${game.turns.length} 轮把他劝了回来。`);
  else if (kind === 'blacklisted') parts.push(`第 ${game.turns.length} 轮，他把你拉黑了。`);
  else parts.push('他还是把那三十万转出去了。');

  if (best && best.delta > 0) {
    parts.push(
      `全场最有力的一句在第 <em>${best.round}</em> 轮——你说「${best.utterance}」，` +
      `信任度那一下涨了 ${best.delta}。`);
    if (kind !== 'persuaded') parts.push('你差的不是方向，是没在他松动的那一刻乘胜追击。');
  } else {
    parts.push('全场没有一句真正推动过他。下一局试着先问问，那笔钱原本是准备干什么用的。');
  }
  return parts.join('');
}

function openReview() {
  const missedKeys = Object.keys(KEYS).filter(
    (k) => !game.turns.some((t) => t.hits.includes(k)));
  const usedPenalties = Object.keys(PENALTIES).filter(
    (p) => game.turns.some((t) => t.hits.includes(p)));
  const best = game.turns.reduce(
    (a, b) => (b.delta > (a ? a.delta : -Infinity) ? b : a), null);
  const peak = game.turns.reduce((m, t) => Math.max(m, t.trust), game.trust);
  const kind = game.ending ? game.ending.kind : 'transferred';
  const meta = ENDINGS[kind];

  const view = document.createElement('section');
  view.className = 'review';
  view.setAttribute('role', 'dialog');
  view.setAttribute('aria-label', '复盘');
  view.innerHTML = `
    <header class="navbar">
      <button class="navback" id="reviewBack" type="button" aria-label="返回"></button>
      <h1>复盘</h1>
    </header>
    <div class="review-body">
      <div class="summary ${kind}">
        <div class="kind"></div>
        <p class="copy"></p>
      </div>

      <div class="group">
        <div class="group-title">信任度</div>
        <div class="panel">
          <canvas id="chart"></canvas>
          <p class="legend">一根蜡烛是一轮，红涨绿跌，实体是这一轮信任度的起落。细横线是判分给出的分数——它与实体端点的落差，就是每轮的信任流失，以及开局封顶时存进蓄势池、第 4 轮起逐轮释放的那部分。</p>
        </div>
      </div>

      <div class="group">
        <div class="group-title">逐轮</div>
        <div class="panel" id="roundsList"></div>
      </div>

      <div class="group" id="missedWrap">
        <div class="group-title">没用上的钥匙</div>
        <div class="panel" id="missedList"></div>
      </div>

      <div class="group" id="penaltyWrap" hidden>
        <div class="group-title">踩过的坑</div>
        <div class="panel" id="penaltyList"></div>
      </div>

      <div class="actions">
        <button id="makeCard">生成分享卡</button>
        <button class="plain" id="restart">再来一局</button>
      </div>
      <div id="cardWrap"></div>
    </div>`;

  document.body.appendChild(view);
  view.querySelector('.summary .kind').textContent = meta.title;
  view.querySelector('.summary .copy').innerHTML = verdictCopy();

  // 直接量、直接画：读 clientWidth 本身就会强制布局，不必等下一帧
  const chart = view.querySelector('#chart');
  const w = chart.clientWidth;
  const h = chart.clientHeight;
  paintKline(fitCanvas(chart, w, h), { x: 10, y: 12, w: w - 20, h: h - 26 }, {
    turns: game.turns,
    threshold: game.threshold,
    slots: Math.max(game.turns.length, 6),
    palette: palette(),
    axis: true,
  });

  const list = view.querySelector('#roundsList');
  game.turns.forEach((t) => {
    const row = document.createElement('div');
    row.className = 'round' + (best && t.round === best.round && t.delta > 0 ? ' pivot' : '');

    const no = document.createElement('div');
    no.className = 'no num';
    no.textContent = String(t.round);

    const body = document.createElement('div');
    const said = document.createElement('div');
    said.className = 'said';
    said.textContent = t.utterance;
    const tags = document.createElement('div');
    tags.className = 'tags';
    hitTags(t.hits, t.grounded).forEach((x) => tags.appendChild(x));
    body.append(said, tags);

    const score = document.createElement('div');
    score.className = 'score num';
    score.style.color = t.delta > 0 ? 'var(--wx-rise)'
      : t.delta < 0 ? 'var(--wx-fall)' : 'var(--wx-sub)';
    score.textContent = t.delta > 0 ? `+${t.delta}` : String(t.delta);
    const after = document.createElement('span');
    after.className = 'after';
    after.textContent = `→ ${t.trust}`;
    score.appendChild(after);

    row.append(no, body, score);
    list.appendChild(row);
  });

  const missed = view.querySelector('#missedList');
  if (!missedKeys.length) {
    missed.innerHTML = '<p class="empty">三把钥匙你都用到了。</p>';
  } else {
    missedKeys.forEach((k) => missed.appendChild(tipCard(KEYS[k])));
  }

  if (usedPenalties.length) {
    view.querySelector('#penaltyWrap').hidden = false;
    const box = view.querySelector('#penaltyList');
    usedPenalties.forEach((p) => box.appendChild(tipCard(PENALTIES[p])));
  }

  view.querySelector('#restart').onclick = () => location.reload();
  view.querySelector('#makeCard').onclick = () => makeCard(view, { peak });
  view.querySelector('#reviewBack').onclick = () => view.remove();
}

function tipCard(meta) {
  const card = document.createElement('div');
  card.className = 'tip';
  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = meta.name;
  const p = document.createElement('p');
  p.textContent = meta.tip;
  card.append(title, p);
  return card;
}

// ── 分享卡 ──────────────────────────────────────────────────

// 避头尾：canvas 没有浏览器的中文断行规则，不管的话「——」会被劈成两半
const NO_LINE_START = '，。、；：？！」）】》…—';

function wrapText(ctx, text, maxWidth) {
  const lines = [];
  let line = '';
  for (const ch of text) {
    if (ctx.measureText(line + ch).width > maxWidth && line) {
      if (NO_LINE_START.includes(ch)) {
        lines.push(line + ch);
        line = '';
        continue;
      }
      lines.push(line);
      line = ch;
    } else {
      line += ch;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function makeCard(view, stats) {
  const W = 640;
  const pad = 48;
  const COPY_TOP = pad + 560;
  const LINE_H = 38;
  const c = palette();
  const kind = game.ending ? game.ending.kind : 'transferred';
  const meta = ENDINGS[kind];

  const wrap = view.querySelector('#cardWrap');
  wrap.innerHTML = '<canvas id="card"></canvas>';
  const canvas = view.querySelector('#card');

  // 先量结论那段话要占几行，再定卡片多高
  const copy = verdictCopy().replace(/<\/?em>/g, '');
  const probe = canvas.getContext('2d');
  probe.font = `400 21px ${c.sans}`;
  const copyLines = wrapText(probe, copy, W - pad * 2);
  const H = COPY_TOP + copyLines.length * LINE_H + 72;

  const ctx = fitCanvas(canvas, W, H);

  ctx.fillStyle = c.white;
  ctx.fillRect(0, 0, W, H);
  ctx.textBaseline = 'top';
  ctx.textAlign = 'left';

  ctx.fillStyle = c.sub;
  ctx.font = `500 15px ${c.sans}`;
  ctx.fillText('AI 反诈劝阻', pad, pad);

  ctx.fillStyle = kind === 'persuaded' ? c.brand : c.rise;
  ctx.font = `600 52px ${c.sans}`;
  ctx.fillText(meta.title, pad, pad + 40);

  ctx.fillStyle = c.sub;
  ctx.font = `400 19px ${c.sans}`;
  ctx.fillText(meta.sub, pad, pad + 110);

  const figures = [
    ['轮次', String(game.turns.length)],
    ['信任度峰值', String(stats.peak)],
    ['命中钥匙', new Set(game.turns.flatMap((t) => t.hits.filter((h) => KEYS[h]))).size + ' / 3'],
  ];
  figures.forEach(([label, value], i) => {
    const x = pad + i * ((W - pad * 2) / 3);
    ctx.fillStyle = c.sub;
    ctx.font = `400 13px ${c.sans}`;
    ctx.fillText(label, x, pad + 176);
    ctx.fillStyle = c.text;
    ctx.font = `600 32px ${c.mono}`;
    ctx.fillText(value, x, pad + 196);
  });

  paintKline(ctx, { x: pad, y: pad + 258, w: W - pad * 2, h: 236 }, {
    turns: game.turns,
    threshold: game.threshold,
    slots: Math.max(game.turns.length, 6),
    palette: c,
    axis: true,
    labelSize: 13,
    maxWidth: 30,
  });

  ctx.strokeStyle = c.line;
  ctx.beginPath();
  ctx.moveTo(pad, pad + 524);
  ctx.lineTo(W - pad, pad + 524);
  ctx.stroke();

  ctx.fillStyle = '#4a4a4a';
  ctx.font = `400 21px ${c.sans}`;
  copyLines.forEach((line, i) => ctx.fillText(line, pad, COPY_TOP + i * LINE_H));

  ctx.fillStyle = c.sub;
  ctx.font = `400 14px ${c.sans}`;
  if (game.contestId) ctx.fillText(`参赛编号 ${game.contestId}`, pad, H - 42);
  ctx.textAlign = 'right';
  ctx.fillText('十二轮，劝住一个人', W - pad, H - 42);

  canvas.toBlob((blob) => {
    if (!blob) return;
    const p = document.createElement('p');
    p.className = 'savehint';
    const a = document.createElement('a');
    a.className = 'savelink';
    a.href = URL.createObjectURL(blob);
    a.download = `反诈劝阻-${meta.title}.png`;
    a.textContent = '保存图片';
    p.append(a, document.createTextNode('，手机上也可以长按上图保存'));
    wrap.appendChild(p);
  }, 'image/png');
}

// ── 开局 ────────────────────────────────────────────────────

// 请求在首页就发出去了。玩家点开会话时开场白通常已经到手，
// 「首屏 ≤3 秒」是被会话列表那一屏顺手买的单。
const ready = (async () => {
  const resp = await fetch('/api/game/start', { method: 'POST' });
  const data = await resp.json();
  game.token = data.token;
  game.opening = data.opening;
  game.trust = data.trust;
  game.threshold = data.win_threshold;
  game.maxRounds = data.remaining;
  game.remaining = data.remaining;
  game.contestId = data.contest_id || '';
})();

function showScreen(id) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.toggle('on', s.id === id));
}

async function enterGame() {
  showScreen('chat');
  if (game.entered) return;

  const row = $('openChen');
  try {
    await ready;
  } catch (e) {
    showScreen('home');
    row.querySelector('.row-preview').textContent = '连不上服务，确认服务已启动后重试';
    return;
  }

  game.entered = true;
  row.querySelector('.badge').remove();
  document.querySelector('.nav-count').remove();
  $('trust').textContent = String(game.trust);
  $('remaining').textContent = String(game.remaining);
  paintTrust();
  divider('下午 2:47');
  say('them', game.opening);
  $('say').focus();
}

$('openChen').addEventListener('click', enterGame);
$('openChen').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); enterGame(); }
});
$('backHome').addEventListener('click', () => showScreen('home'));

$('composer').addEventListener('submit', (e) => {
  e.preventDefault();
  if (game.busy) return;
  const input = $('say');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  playTurn(text);
});

// 回车发送。isComposing 那一层不能少：中文输入法用回车确认候选词，
// 少了它，玩家选词时会把半句话发出去。
$('say').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.isComposing) {
    e.preventDefault();
    $('composer').requestSubmit();
  }
});
