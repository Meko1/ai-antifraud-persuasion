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

// 转账金额与收款方在这儿写死：它们是剧本设定（app/gateway.py 的 SCAM_SCRIPT），
// 不是判分参数，服务端也不下发。
const TOTAL = 300000;
// 拦下那一档里仍有一小笔钱被转走。这是骗局的标准剧本——先小额取信——
// 代价是玩家表现不错、结局仍有人损失。取真实。
const TEST_TRANSFER = 20000;
const PAYEE = '转账给 启航投顾-李';

const money = (n) =>
  '¥' + n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// 结局是一道阶梯，不是胜负（CONTEXT.md「结局」）。四档量的是他最后有多信你，
// 对玩家呈现为"你救回了多少钱"——金额是这件事在现实里的记法。
// 排序的反直觉之处：拖住一分没转，仍排在已转出一小笔的拦下之下，
// 因为"我再想想"多半是打发你的话，不是让步。
const ENDINGS = {
  persuaded: {
    tier: '劝住', title: '他把钱留住了',
    savedCopy: '三十万，一分没转',
    receipt: 'void', amount: TOTAL,
  },
  intercepted: {
    tier: '拦下', title: '他只转了两万',
    savedCopy: '转出两万试水，保住二十八万',
    receipt: 'sent', amount: TEST_TRANSFER,
  },
  stalled: {
    tier: '拖住', title: '他说再想想',
    savedCopy: '钱一分没动，也一分没保住',
    receipt: 'hold', amount: TOTAL,
  },
  transferred: {
    tier: '转账', title: '他还是转走了',
    savedCopy: '三十万，三点整全部到账',
    receipt: 'sent', amount: TOTAL,
  },
  // 被拉黑不入档：连凭证都没有，你不会知道他最后把钱转没转
  blacklisted: {
    tier: '被拉黑', title: '他把你拉黑了',
    savedCopy: '你连他最后转没转都不会知道',
    receipt: null, amount: TOTAL,
  },
};

const ERRORS = {
  invalid_state: '这一局放得太久了，得重开一局',
  upstream_unavailable: '消息没发出去，再说一次试试',
  rate_limited: '这会儿人有点多，等两秒再说',
  internal: '出了点岔子，再说一次试试',
  network: '网络断了，这句没发出去',
};

const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const sleep = (ms) => new Promise((r) => setTimeout(r, REDUCED ? 0 : ms));

// ── 状态 ────────────────────────────────────────────────────

const game = {
  token: '',
  contestId: '',
  opening: '',
  trust: 0,
  mood: 'irritated',
  threshold: 80,
  maxRounds: 12,
  remaining: 12,
  turns: [],      // {round, utterance, reply, lines, hits, grounded, delta, trust, before, pool}
  ending: null,   // {kind, trust, lines}
  quote: null,    // 分享卡上那句话 {round, text}
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

// ── K 线（复盘）─────────────────────────────────────────────
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
    bg: v('--wx-bg'), red: v('--wx-red'),
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

function msgRow(who, text) {
  const row = document.createElement('div');
  row.className = `msg ${who}`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  row.append(avatar(who), bubble);
  return row;
}

function say(who, text) {
  thread.appendChild(msgRow(who, text));
  toBottom();
}

/** 「对方正在输入」的那个气泡。他说完的每一句都插在它前面。 */
function typingRow() {
  const row = document.createElement('div');
  row.className = 'msg them';
  const bubble = document.createElement('div');
  bubble.className = 'bubble typing';
  row.append(avatar('them'), bubble);
  thread.appendChild(row);
  toBottom();
  return row;
}

/** 逐句下发的排队器。
 *
 * 后端已按句切好（ADR-0004），但 SSE 常常一口气把三句一起推过来。
 * 真人在微信上是连发三条短消息，中间有打字的间隔——所以渲染节奏
 * 和网络节奏必须脱钩：句子到了先排队，按 260–420ms 一条往外放。
 */
function paced(anchor) {
  let chain = Promise.resolve();
  let count = 0;
  return {
    push(text) {
      chain = chain.then(async () => {
        if (count++) await sleep(260 + Math.random() * 160);
        thread.insertBefore(msgRow('them', text), anchor);
        toBottom();
      });
    },
    drain: () => chain,
  };
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

/** 剧情旁白。李老师在群里催的那一下走这个位置——
 *  它不是判分反馈，是一个看得见的施压来源。 */
function narrate(text) {
  sysnote([text]).classList.add('push');
}

// 对局中不给数字，只给一个词。玩家要判断他到了哪一档，只能靠读他说的话——
// 而这正是这个作品想教的那件事。数字与标签全部留到复盘。
const MOODS = {
  guarded: '戒备',
  irritated: '烦躁',
  wavering: '有点松动',
  softening: '松动了',
};

function paintMood(mood) {
  const el = $('mood');
  const word = MOODS[mood] || MOODS.guarded;
  if (el.textContent !== word) {
    el.textContent = word;
    el.classList.remove('turn');
    void el.offsetWidth;  // 强制重排，让同名动画能第二次播
    el.classList.add('turn');
  }

  // 细条不带数字也不闪：它只是个余光里的东西，用来兜住"完全没有反馈"的茫然
  const pct = Math.max(0, Math.min(100, game.trust));
  const fill = $('trustFill');
  fill.style.width = pct + '%';
  fill.className = 'st-fill' + (pct < 20 ? ' danger' : pct < 45 ? ' low' : '');
  $('trustGoal').style.left = game.threshold + '%';
}

// ── 一轮 ────────────────────────────────────────────────────

async function playTurn(utterance) {
  game.busy = true;
  $('send').disabled = true;
  $('say').disabled = true;

  say('me', utterance);
  const typing = typingRow();
  const queue = paced(typing);

  let resp;
  try {
    resp = await fetch('/api/game/turn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: game.token, utterance }),
    });
  } catch (e) {
    typing.remove();
    return failTurn('network');
  }

  if (!resp.ok || !resp.body) {
    typing.remove();
    return failTurn('internal');
  }

  let round = null;
  const spoken = [];
  let score = null;
  let failed = null;

  try {
    for await (const ev of readEvents(resp)) {
      if (ev.name === 'meta') {
        round = ev.data.round;
        game.remaining = ev.data.remaining;
        $('remaining').textContent = String(ev.data.remaining);
      } else if (ev.name === 'sentence') {
        spoken.push(ev.data.text);
        queue.push(ev.data.text);
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

  // 等他把话说完。判分那一跳绝不能抢在最后一句前面落地
  await queue.drain();
  typing.remove();

  if (failed) return failTurn(failed);

  if (score) {
    game.turns.push({
      round: round || game.turns.length + 1,
      utterance,
      reply: spoken.join(''),
      // 逐句留着，不只留拼好的那一整段：分享卡要挑的是**其中一句**
      lines: spoken.slice(),
      hits: score.hits,
      grounded: score.grounded,
      delta: score.delta,
      trust: score.trust,
      before: game.trust,
      pool: score.pool,
    });
    game.trust = score.trust;
    paintMood(score.mood);
    // 判分卡不在对局中出现：标签与分数一律留到复盘。
    // 边打边给答案等于把攻略印在屏幕上——玩家两轮就学会照着清单刷分，
    // 从此不再读人。要读的东西只有一样：他说的话。
    if (score.pressure) narrate('李老师又在群里催了一遍');
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

const RECEIPT_FOOT = {
  sent: '15:00 已被对方接收',
  void: '已取消转账',
  hold: '还没点发送',
};

/** 转账凭证。他截了张图发过来——微信里人人都干过这事。
 *
 * 四档结局共用这一张卡，翻的是同一个位：橙的是已被接收，灰的是没点下去。
 * 拦下与转账都是橙的，差别只在金额——两万和三十万，一眼就是不同的结局。
 * 拖住是灰的但金额没划掉：转账页面填好了停在那儿，他没点。
 * 整局对局赌的就是这一位，所以它值得是这一屏上唯一一件重物。
 */
function receipt(state, amount) {
  const text = money(amount);
  const el = document.createElement('div');
  el.className = 'receipt' + (state === 'sent' ? '' : ' ' + state);
  el.innerHTML =
    '<div class="receipt-body">' +
      '<div class="receipt-icon" aria-hidden="true">¥</div>' +
      '<div><div class="receipt-amt"></div><div class="receipt-to"></div></div>' +
    '</div><div class="receipt-foot"></div>';
  el.querySelector('.receipt-amt').textContent = text;
  el.querySelector('.receipt-to').textContent = PAYEE;
  el.querySelector('.receipt-foot').textContent = RECEIPT_FOOT[state];
  el.setAttribute('role', 'img');
  el.setAttribute('aria-label',
    `转账凭证截图：${text}，${PAYEE}，${RECEIPT_FOOT[state]}`);
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

async function finish() {
  const kind = game.ending.kind;
  $('composer').hidden = true;

  // 最后几句和前面十二轮一样，一句一个气泡。结局不该是"突然弹出一整段"
  for (const [i, line] of (game.ending.lines || []).entries()) {
    if (i) await sleep(260 + Math.random() * 160);
    say('them', line);
  }
  await sleep(400);

  // 结局不另起一块 UI，它就是这段对话里的最后一件东西。
  const meta = ENDINGS[kind] || ENDINGS.transferred;
  if (!meta.receipt) {
    // 被拉黑之后微信显示的就是这张卡。开局那句「对方不是你的朋友」在这里合上了口：
    // 你一直是个陌生人，现在连话都递不进去了。
    const tip = document.createElement('p');
    tip.className = 'strangertip';
    tip.textContent = '对方开启了朋友验证，你还不是他（她）朋友。';
    thread.appendChild(tip);
  } else {
    photo(receipt(meta.receipt, meta.amount));
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
  else if (kind === 'intercepted') parts.push('他只按老师说的转了两万试水，剩下的二十八万你按住了。');
  else if (kind === 'stalled') parts.push('他把这事推到了明天——你争到的是时间，不是他的决定。');
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
  const kind = game.ending ? game.ending.kind : 'transferred';
  const meta = ENDINGS[kind] || ENDINGS.transferred;

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
        <div class="saved"></div>
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

      <div class="group">
        <div class="group-title">名场面 · 挑一句他说的话</div>
        <div class="panel" id="quoteList" role="radiogroup" aria-label="名场面"></div>
      </div>

      <div class="actions">
        <button id="makeCard">生成分享卡</button>
        <button class="plain" id="restart">再来一局</button>
      </div>
      <div id="cardWrap"></div>
    </div>`;

  document.body.appendChild(view);
  view.querySelector('.summary .kind').textContent = `${meta.tier} · ${meta.title}`;
  view.querySelector('.summary .saved').textContent = meta.savedCopy;
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

  paintQuotes(view);

  view.querySelector('#restart').onclick = () => location.reload();
  view.querySelector('#makeCard').onclick = () => makeCard(view);
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

// ── 名场面 ──────────────────────────────────────────────────
//
// 人们分享的不是自己的成绩，是 AI 说的那句话的截图。所以分享卡的主体是
// **他说过的一句话**，成绩退到卡底一行小字。玩家自己挑那一句——
// 哪句戳中了他，只有他知道，任何自动挑选都不如他准。

const JARGON = [
  '老师', '内部', '消息', '涨停', '翻倍', '行规', '散户', '机构', '补仓', '割肉',
  '满仓', '加仓', '踏空', '解冻', '保证金', '跟单', '账户', '手续费', '出金',
  '这波', '行情', '免责', '协议', '收益',
];

/** 按句切。后端已按句下发（ADR-0004），开场白与结局台词还得自己切一遍。 */
function sentences(text) {
  const out = [];
  let cur = '';
  for (const ch of text) {
    cur += ch;
    if ('。！？…'.includes(ch)) {
      out.push(cur.trim());
      cur = '';
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

/** 他这一局说过的所有句子，按时间序，去重。 */
function hisLines() {
  const raw = [];
  sentences(game.opening).forEach((text) => raw.push({ round: 0, text }));
  game.turns.forEach((t) =>
    (t.lines || sentences(t.reply || '')).forEach((line) =>
      sentences(line).forEach((text) => raw.push({ round: t.round, text }))));
  ((game.ending && game.ending.lines) || []).forEach((line) =>
    sentences(line).forEach((text) => raw.push({ round: null, text })));

  const seen = new Set();
  return raw.filter(({ text }) => {
    // 太短的没有信息量，太长的在卡上就不是"一句话"了
    if (text.length < 8 || text.length > 44 || seen.has(text)) return false;
    seen.add(text);
    return true;
  });
}

/** 默认挑哪一句。挑不准也没关系——真正的选择权在下面那张列表上，
 *  这个函数只负责让默认值不尴尬。 */
function punch(text) {
  let score = 20 - Math.abs(text.length - 20);        // 20 字上下最像一句能被截图的话
  if (text.includes('你')) score += 6;                 // 冲着你来的话最有对峙感
  if (JARGON.some((w) => text.includes(w))) score += 5; // 骗局黑话是行内人一眼认得出的那部分
  if (/[，,]/.test(text)) score += 2;                  // 有转折的句子比平铺的一句有味道
  return score;
}

function quoteLabel(quote) {
  if (quote.round === 0) return '开场';
  return quote.round === null ? '最后' : `第 ${quote.round} 轮`;
}

function paintQuotes(view) {
  const box = view.querySelector('#quoteList');
  const lines = hisLines();
  if (!lines.length) {
    // 兜底台词全程顶上时会走到这里。没有名场面就没有分享卡——
    // 与其出一张只有成绩的卡，不如让按钮明说。
    box.innerHTML = '<p class="empty">这一局他没留下能单独拎出来的话。</p>';
    game.quote = null;
    const btn = view.querySelector('#makeCard');
    btn.disabled = true;
    btn.textContent = '没有可上卡的话';
    return;
  }

  const top = lines.slice().sort((a, b) => punch(b.text) - punch(a.text))[0];
  game.quote = game.quote || top;

  lines.forEach((quote) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'quote';
    row.setAttribute('role', 'radio');

    const no = document.createElement('span');
    no.className = 'qno';
    no.textContent = quoteLabel(quote);
    const text = document.createElement('span');
    text.className = 'qtext';
    text.textContent = quote.text;
    row.append(no, text);

    const select = () => {
      game.quote = quote;
      box.querySelectorAll('.quote').forEach((el) =>
        el.setAttribute('aria-checked', String(el === row)));
      // 已经出过图就当场换一张，省得玩家再点一次"生成"
      if (view.querySelector('#card')) makeCard(view);
    };
    row.setAttribute('aria-checked', String(quote.text === game.quote.text));
    row.onclick = select;
    box.appendChild(row);
  });
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

/** 圆角矩形。ctx.roundRect 在旧一点的 iOS Safari 上还没有，自己描一遍。 */
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function tierColor(kind, c) {
  if (kind === 'persuaded' || kind === 'intercepted') return c.brand;
  if (kind === 'stalled') return c.gray;
  return c.red;
}

/** 分享卡 = 他那句话的聊天截图。
 *
 * 上一版是一张成绩单（K 线 + 轮次 + 信任度峰值），而人们不发自己的成绩，
 * 发的是 AI 说的话——一个同行看到「免责协议那是行规，你外行不懂」会心头一紧，
 * 因为他上周刚听客户说过差不多的话。所以主体让给那句话，成绩退到卡底一行。
 * K 线没有丢，它留在复盘里——那是给认真打的人和评审看的东西。
 */
function makeCard(view) {
  const W = 640;
  const pad = 44;
  const AV = 60;              // 头像
  const GAP = 16;             // 头像与气泡的间距
  const BUB_PAD_X = 26;
  const BUB_PAD_Y = 24;
  const QUOTE_SIZE = 30;
  const QUOTE_LH = 48;
  const c = palette();
  const kind = game.ending ? game.ending.kind : 'transferred';
  const meta = ENDINGS[kind] || ENDINGS.transferred;
  const quote = game.quote;
  if (!quote) return;

  const wrap = view.querySelector('#cardWrap');
  wrap.innerHTML = '<canvas id="card"></canvas>';
  const canvas = view.querySelector('#card');

  // 先量那句话要占几行，再定卡片多高——句子长短决定卡片高矮，不留空档
  const bubbleX = pad + AV + GAP;
  const bubbleMax = W - pad - bubbleX;
  const probe = canvas.getContext('2d');
  probe.font = `600 ${QUOTE_SIZE}px ${c.sans}`;
  const quoteLines = wrapText(probe, quote.text, bubbleMax - BUB_PAD_X * 2);
  const bubbleW = Math.max(
    ...quoteLines.map((l) => probe.measureText(l).width)) + BUB_PAD_X * 2;
  const bubbleH = quoteLines.length * QUOTE_LH + BUB_PAD_Y * 2 - (QUOTE_LH - QUOTE_SIZE);

  const BUB_TOP = pad + 46;
  const FOOT_TOP = BUB_TOP + bubbleH + 132;
  const H = FOOT_TOP + 82;   // 底部留白与左右的 pad 对齐，卡才不显得下坠

  const ctx = fitCanvas(canvas, W, H);

  // 底色用聊天背景的灰：整张卡就该看着像一张微信截图，而不像一张海报
  ctx.fillStyle = c.bg;
  ctx.fillRect(0, 0, W, H);
  ctx.textBaseline = 'top';
  ctx.textAlign = 'left';

  ctx.fillStyle = c.sub;
  ctx.font = `500 15px ${c.sans}`;
  ctx.fillText('AI 反诈劝阻', pad, pad);

  // 头像：和对话里那个是同一个（.av-chen）
  ctx.fillStyle = '#6f8bb5';
  roundRect(ctx, pad, BUB_TOP, AV, AV, 6);
  ctx.fill();
  ctx.fillStyle = '#fff';
  ctx.font = `500 25px ${c.sans}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('陈', pad + AV / 2, BUB_TOP + AV / 2 + 1);
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';

  // 气泡：白底、5px 圆角、左上一个小尖角，和界面里的一模一样
  ctx.fillStyle = c.white;
  roundRect(ctx, bubbleX, BUB_TOP, bubbleW, bubbleH, 8);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(bubbleX, BUB_TOP + 22);
  ctx.lineTo(bubbleX - 9, BUB_TOP + 30);
  ctx.lineTo(bubbleX, BUB_TOP + 40);
  ctx.closePath();
  ctx.fill();

  ctx.fillStyle = c.text;
  ctx.font = `600 ${QUOTE_SIZE}px ${c.sans}`;
  quoteLines.forEach((line, i) =>
    ctx.fillText(line, bubbleX + BUB_PAD_X, BUB_TOP + BUB_PAD_Y + i * QUOTE_LH));

  ctx.fillStyle = c.sub;
  ctx.font = `400 16px ${c.sans}`;
  ctx.fillText(`老陈 · ${quoteLabel(quote)}`, bubbleX, BUB_TOP + bubbleH + 14);

  // 成绩退到这一行：结局那一档 + 你救回了多少钱。金额是这件事在现实里的记法。
  ctx.strokeStyle = 'rgba(0, 0, 0, 0.08)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, BUB_TOP + bubbleH + 62.5);
  ctx.lineTo(W - pad, BUB_TOP + bubbleH + 62.5);
  ctx.stroke();

  const tier = `${meta.tier} · ${meta.title}`;
  ctx.fillStyle = tierColor(kind, c);
  ctx.font = `600 27px ${c.sans}`;
  ctx.fillText(tier, pad, BUB_TOP + bubbleH + 86);
  ctx.fillStyle = c.gray;
  ctx.font = `400 18px ${c.sans}`;
  ctx.fillText(meta.savedCopy, pad, BUB_TOP + bubbleH + 126);

  // 钩子放在右下角，正好接住下一步的"接话"入口
  ctx.fillStyle = c.sub;
  ctx.font = `400 14px ${c.sans}`;
  if (game.contestId) ctx.fillText(`参赛编号 ${game.contestId}`, pad, FOOT_TOP + 40);
  ctx.textAlign = 'right';
  ctx.fillStyle = c.gray;
  ctx.font = `500 17px ${c.sans}`;
  ctx.fillText('这句话，你会怎么接？', W - pad, FOOT_TOP + 36);

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

// ── 开局 ────────────────────────────────────────────────────

// 请求在首页就发出去了。玩家点开会话时开场白通常已经到手，
// 「首屏 ≤3 秒」是被会话列表那一屏顺手买的单。
const ready = (async () => {
  const resp = await fetch('/api/game/start', { method: 'POST' });
  const data = await resp.json();
  game.token = data.token;
  game.opening = data.opening;
  game.trust = data.trust;
  game.mood = data.mood;
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
  $('remaining').textContent = String(game.remaining);
  paintMood(game.mood);
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
