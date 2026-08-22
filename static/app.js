/* AI 反诈劝阻 · 前端
 *
 * 你＝老陈的投资顾问（局内他叫你李经理），他＝你的客户。首页那一屏是**你的
 * 工作台**（企业微信 + 券商 CRM）：一条资产异动预警加一张客户档案，就是你
 * 开局手上的全部。点"给陈叔发消息"才进聊天。
 *
 * 他手机上那五条（启航财经群、招行、老伴、小雨、反诈中心）**不在开局**——
 * 它们在复盘里逐条揭晓（`paintPhone`）。原先它们就是首页，等于开局把这一局
 * 要挖的东西全给了玩家，而挖它就是玩法。
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

/* `brief` 与 `tip` 是两句不同的话，不能互相替代（8-22）：
 *
 * - `brief` 开局前给，**只说这个动作是什么**，一句话，不含时机、不含分值、
 *   不含"哪一把更强"。它是课程表，不是攻略。
 * - `tip` 复盘时给，可以讲时机与相对强弱（"说早了它就不是钥匙"
 *   "他越防着你别的招越没用"）——那时候讲才是教学，开打前讲就是泄题。
 *
 * 这条分界正是「给词汇，不给时机」：**词汇是课程，时机是答案。**
 * 任何真实的培训都会先告诉你评分维度叫什么，但不会在你答题时告诉你分数。
 * 往 `brief` 里写"这一招在他戒备时最管用"，这个作品的差异点就白送了。 */
const KEYS = {
  anchor_real_purpose: {
    name: '锚定真实用途',
    brief: '让他自己说出这笔钱本来是干什么用的。',
    tip: '被洗脑的人满脑子是收益率。让他自己说出「这笔钱本来是给孩子办婚礼的」，' +
         '抽象的数字才会变回具体的代价——一线劝阻里最有效的一招，也最难开口问。',
  },
  socratic_question: {
    name: '苏格拉底式提问',
    brief: '不给结论，用提问让他自己发现问题。',
    tip: '直接否定只会激起防御。让他自己去按那个提现键、自己去看账户开户名是谁，' +
         '结论由他自己得出，才立得住。',
  },
  expose_contradiction: {
    name: '指出内部矛盾',
    brief: '用他自己说过的话，指出两件对不上的事。',
    tip: '骗局的话术经不起并置：「内部消息」和「几百人的群」不可能同时成立。' +
         '用他自己说过的话去顶，比引用任何新闻都管用。',
  },
  // 下面四把 8-17 补。前三把都属于"引出他自己说话"的一侧，缺的是另外半套：
  // 先听懂他、确认他到底理解了什么、以及不去替他做决定。
  reflect_feeling: {
    name: '反映式倾听',
    brief: '把他的处境和情绪原样说回去，不追加要求。',
    tip: '把他的处境原样说回去，不追加任何要求。它是唯一能主动把他的防备压下去的动作——' +
         '骂过他之后，别的招都被打折，只有先听他说完才救得回来。',
  },
  support_autonomy: {
    name: '支持自主',
    brief: '明说决定权在他手上，你不替他做主。',
    tip: '「转不转是您的钱，我不替您做主。」它同时是逆反的解药和投顾唯一站得住的合规姿态。' +
         '**他越是竖起防备，别的招越没用，而这一把照常生效**。',
  },
  check_understanding: {
    name: '确认理解',
    brief: '让他自己讲一遍这钱转过去之后会怎么走。',
    tip: '让他自己讲一遍钱转过去之后会怎么走。这是适当性管理的硬要求，' +
         '也是揭穿骗局最狠的一招——他讲不出来。他自己听见自己答不上，比你说一百句都重。',
  },
  informed_warning: {
    name: '有据告知',
    brief: '把你的判断说出口，并且给出依据。',
    tip: '把判断说出口，并且给出依据。投顾对疑似诈骗负有告知义务，该说的时候不说是失职。' +
         '**但说早了它就不是钥匙**：他还硬着的时候，这句和「这是诈骗」没有区别。',
  },
};

const PENALTIES = {
  scold: { name: '责骂', tip: '一旦被骂，他就把你归进「不理解我的人」，从此不再开口。' },
  preach: { name: '说教', tip: '摆数据像上课。他要的不是知识，是一个能下的台阶。' },
  bare_assertion: { name: '空口断言', tip: '「这是诈骗」四个字他这三个月听了无数遍，早免疫了。' },
};

// 合规红线。与 app/scoring.py 的 COMPLIANCE_VALUES 一一对应。
//
// **它们和上面三条不是一类东西，所以不放在同一张表里。** 话术失误是
// "这一轮没劝动他"，合规红线是"**你自己要出事**"——后者的后果不由这一局的
// 输赢承载，复盘因此要单独把它拎出来说，哪怕玩家把三十万全保住了。
const BREACHES = {
  unlicensed_advice: {
    name: '荐股',
    tip: '给出具体标的、买卖方向或产品推荐。投顾无证荐股是执业禁区，' +
         '多家券商因此被罚——而且他当场就会认定你也是来卖东西的。',
  },
  guaranteed_return: {
    name: '承诺收益',
    tip: '保本、稳赚、打包票。它同时是监管红线和一句谎：' +
         '你用王老师的话术去反驳王老师，赢了也是输。',
  },
};

/** 这一局踩了几次红线。判分口径在服务端，前端只认下发的 hits。 */
function breachTurns() {
  return game.turns.filter((t) => t.hits.some((h) => h in BREACHES));
}

/** **真正判成了的那些轮。** 分类失败的轮次按中性记分（`degraded`），
 *  hits 是空的——在任何"你用了几次 / 占几成"的统计里，它和
 *  "玩家这一轮说了句没用的话"**长得一模一样**。
 *
 *  所以凡是拿轮次当分母的地方都要走这里，而不是 `game.turns`：
 *  网关抖一下不该记在人头上。
 *
 *  分子分母必须用同一个集合，否则会算出"你 120%"这种数。
 *
 *  **不适用于**：K 线、轮数、结局文案、逐轮清单——那几处要的是
 *  "这一局实际发生了什么"，降级的那一轮确实发生过，玩家也确实说了那句话。 */
function scoredTurns() {
  return game.turns.filter((t) => !t.degraded);
}

// ── 剧本素材 ────────────────────────────────────────────────────
//
// **金额、收款方、客户档案、揭晓清单原先全写死在这里和 index.html 里。**
// 加第二个场景时那些地方没有一处会提醒你漏改了——于是它们搬去了服务端
// （app/scenario.py 的 `payload`），开局随 /api/game/start 下发。
//
// 这不只是整洁：判分那边按场景换了效力矩阵，界面这边要是还印着三十万和
// 王老师，玩家看到的就是两个不同的骗局拼在一起。
let SCENE = null;

const TOTAL = () => (SCENE ? SCENE.money.total : 300000);
// 拦下那一档里仍有一小笔钱被转走。这是骗局的标准剧本——先小额取信——
// 代价是玩家表现不错、结局仍有人损失。取真实。
const TEST_TRANSFER = () => (SCENE ? SCENE.money.test_transfer : 20000);
const PAYEE = () => (SCENE ? SCENE.money.payee : '');

// 你先发的那一条，聊天窗口的第一条——**他的开场白是在回它**，
// 少了它他就是在回应空气。
// 内容只能有一条信息：账户动了。骗局的一切开局你一概不知道（CONTEXT.md「对局」）。
const PING = () => (SCENE ? SCENE.ping : '');
const peerInitial = () => (SCENE ? SCENE.initial : '陈');
// 状态条上那个「他/她」。周淑琴那一局写「他现在」，玩家一眼看出界面是照别人做的
const peerPronoun = () => (SCENE ? SCENE.pronoun : '他');

const money = (n) =>
  '¥' + n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// 复盘头部那个大数不带角分：转账凭证上要两位小数（那是单据），
// 结算卡上不要（那是结果）。¥300,000.00 里的 .00 只会把字号占掉。
const wholeMoney = (n) => '¥' + n.toLocaleString('zh-CN');

/** 这一局替客户守住了多少钱。
 *
 * 只有劝住与拦下真的留住了钱；拖住、转账、被拉黑都是 0。
 * **0 是这张卡上最该被看见的那个数** —— "钱一分没动，也一分没保住"
 * 那句话说了半天，不如一个 ¥0 来得重。
 */
function savedAmount(kind) {
  if (kind === 'persuaded') return TOTAL();
  if (kind === 'intercepted') return TOTAL() - TEST_TRANSFER();
  return 0;
}

/** 复盘首屏展示的资金状态。
 *
 * 拖住的钱还在账户里，但客户并未放弃转账，所以它不能叫“守住”；
 * 被拉黑后连资金是否已转出都无法确认，也不能用一个虚假的 ¥0 代替未知。
 *
 * **「确认保住」这四个字 8-22 拿掉了。** 这一局判的是他最后有多信你，
 * 而信任度跨过一条线**不等于钱安全了**：这套判分闭集里七把钥匙全是问法，
 * 没有任何一把是"劰住这笔转账""核验收款方""陪他打 96110"——
 * 玩家从头到尾没做过任何一个真实处置动作。拿一句"确认保住"去总结它，
 * 是在教一件现实里不成立的事。
 *
 * 改的是文案不是机制（判分闭集一行没动）：金额仍然是叙事外壳，
 * 只是不再自称是业务结果。真实处置差哪几步，写在复盘末尾那张清单里。
 */
function resultAmount(kind) {
  if (kind === 'persuaded') {
    return { value: wholeMoney(TOTAL()), label: '他最后没按下确认' };
  }
  if (kind === 'intercepted') {
    return { value: wholeMoney(TOTAL() - TEST_TRANSFER()), label: '其余的他暂时按住了' };
  }
  if (kind === 'stalled') {
    return { value: wholeMoney(TOTAL()), label: '暂未转出，风险尚未解除' };
  }
  if (kind === 'blacklisted') {
    return { value: '状态未知', label: '客户仍可能继续转账，你已无法跟进' };
  }
  return { value: '¥0', label: `${wholeMoney(TOTAL())} 已全部转出` };
}

// 结局是一道阶梯，不是胜负（CONTEXT.md「结局」）。四档量的是他最后有多信你，
// 对玩家呈现为"你救回了多少钱"——金额是这件事在现实里的记法。
// 排序的反直觉之处：拖住一分没转，仍排在已转出一小笔的拦下之下，
// 因为"我再想想"多半是打发你的话，不是让步。
// 结局是一道阶梯，不是胜负（CONTEXT.md「结局」）。四档量的是他最后有多信你，
// 对玩家呈现为"你救回了多少钱"——金额是这件事在现实里的记法。
// 排序的反直觉之处：拖住一分没转，仍排在已转出一小笔的拦下之下，
// 因为"我再想想"多半是打发你的话，不是让步。
//
// 三档的文案（档位名 / 标题 / 那句说明）随场景走：老陈是"他还是转走了"，
// 周阿姨是"她还是转走了"。凭证形态与金额不随场景变，它们是机制。
const RECEIPT = {
  persuaded: 'void', intercepted: 'sent', stalled: 'hold',
  transferred: 'sent', blacklisted: null,
};

// 复盘页的四色语义（设计稿的 data-tone）。见 openReview 里那段注。
const TONES = {
  persuaded: 'green',
  intercepted: 'gold',
  stalled: 'plain',
  transferred: 'rust',
  blacklisted: 'rust',
};

function endingMeta(kind) {
  const copy = (SCENE && SCENE.endings[kind]) || {};
  return {
    tier: copy.tier || '转账',
    title: copy.title || '他还是转走了',
    savedCopy: copy.saved || '',
    receipt: RECEIPT[kind] !== undefined ? RECEIPT[kind] : 'sent',
    amount: kind === 'intercepted' ? TEST_TRANSFER() : TOTAL(),
  };
}

const ERRORS = {
  invalid_state: '这一局放得太久了，得重开一局',
  upstream_unavailable: '消息没发出去，再说一次试试',
  rate_limited: '这会儿人有点多，等两秒再说',
  internal: '出了点岔子，再说一次试试',
  network: '网络断了，这句没发出去',
  // 服务端挡下了一张已经打完的令牌。正常玩不会碰到——碰到多半是这一轮的
  // 结果没能回到手上（网络在中途断了），而服务端那边已经算完了。
  replayed: '这一轮已经算过了，页面和他那边对不上，得重开一局',
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

  // 开局那条线。**加它是为了把图上那片空白变成信息。**
  // 纵轴固定 0–100，而多数局子收在 40 以下，于是上面大半张图是空的，
  // 看着像没画完。有了这条线，同一片空白立刻在说一件事：
  // 你是把他往上推了，还是一路把他推下去了——一眼就看得出来。
  if (opts.start != null) {
    ctx.strokeStyle = c.line;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, Math.round(py(opts.start)) + 0.5);
    ctx.lineTo(x + w, Math.round(py(opts.start)) + 0.5);
    ctx.stroke();
    if (opts.axis) {
      ctx.fillStyle = c.note || c.gray;
      ctx.font = `400 ${opts.labelSize || 10}px ${c.sans}`;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'bottom';
      ctx.fillText(`开局 ${opts.start}`, x + w - 2, py(opts.start) - 3);
    }
  }

  ctx.strokeStyle = c.goal;
  ctx.setLineDash([3, 4]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, py(threshold) + 0.5);
  ctx.lineTo(x + w, py(threshold) + 0.5);
  ctx.stroke();
  ctx.setLineDash([]);

  if (opts.axis) {
    // 标线本身用鲜亮的 goal，字用专门配出的 goalText——同一个颜色兼职当
    // 线又当字，字那份对比度不够看（2.42:1，门槛 4.5），这是它看着发虚的
    // 原因之一
    ctx.fillStyle = c.goalText || c.goal;
    ctx.font = `600 ${opts.labelSize || 10}px ${c.sans}`;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'bottom';
    ctx.fillText(`劝住 ${threshold}`, x + 2, py(threshold) - 3);
  }

  const count = Math.max(slots, turns.length, 1);
  const step = w / count;
  const width = Math.max(4, Math.min(opts.maxWidth || 16, step * 0.5));
  const radius = Math.min(2.5, width / 2);

  turns.forEach((t, i) => {
    const cx = x + step * (i + 0.5);
    const open = t.before;
    const close = t.trust;
    const raw = Math.max(0, Math.min(100, open + t.delta));
    const color = close > open ? c.rise : close < open ? c.fall : c.sub;

    // 影线：2px、圆头——原来 1px 加透明度叠在实体上会糊成一团浅色，
    // 换成不透明的细线，粗细不够就直接调宽度，不靠透明度撑视觉重量
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(cx, py(Math.max(open, close, raw)));
    ctx.lineTo(cx, py(Math.min(open, close, raw)));
    ctx.stroke();

    // 实体：4px 圆角——原来是直角矩形，跟界面其余地方（气泡、徽章）
    // 全是圆角的语言对不上，这块地方最扎眼地显得"没做完"
    const top = py(Math.max(open, close));
    const bottom = py(Math.min(open, close));
    ctx.fillStyle = color;
    roundRect(ctx, cx - width / 2, top, width, Math.max(3, bottom - top), radius);
    ctx.fill();

    // 判分线：原来是半透明叠加，颜色会随底下是实体还是空白而变深浅不一，
    // 换成固定的中性色、不透明，同一条线在任何底色上都是同一个视觉重量。
    // 只画影线的话，蓄势池「释放」那一侧会看不见，所以这条线不能省
    ctx.strokeStyle = c.note;
    ctx.lineWidth = 1.5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(cx - width * 0.6, py(raw));
    ctx.lineTo(cx + width * 0.6, py(raw));
    ctx.stroke();
  });

  ctx.strokeStyle = c.line;
  ctx.lineWidth = 1.5;
  ctx.lineCap = 'round';
  for (let i = turns.length; i < count; i++) {
    const cx = x + step * (i + 0.5);
    ctx.beginPath();
    ctx.moveTo(cx - width / 2, y + h);
    ctx.lineTo(cx + width / 2, y + h);
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
    // 画在图上的小字要能读：--wx-sub 对白底只有 2.12:1、--gold 只有 2.42:1，
    // 门槛是 4.5。图形色（线、蜡烛）照旧鲜亮，文字色单独取深的那一份——
    // goalText 就是给"劝住 80"那行字用的，标线本身仍然用 goal
    note: v('--wx-note'), goalText: v('--gold-text'),
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
  el.textContent = who === 'them' ? peerInitial() : '我';
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
  const rows = [];
  return {
    push(text) {
      chain = chain.then(async () => {
        if (count++) await sleep(260 + Math.random() * 160);
        const row = msgRow('them', text);
        rows.push(row);
        thread.insertBefore(row, anchor);
        toBottom();
      });
    },
    drain: () => chain,
    /** 这一轮作废时把已经播出去的气泡收回来。
     *
     * 少了它，出错的那一轮会留下一段"他确实回过话"的聊天记录，而服务端根本
     * 没记这一轮（令牌没推进）。玩家再说一句，老陈完全不记得刚才那段——
     * 聊天记录与复盘（game.turns 是它唯一的数据源）当场分叉。 */
    rollback() {
      rows.forEach((row) => row.remove());
      rows.length = 0;
    },
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
  const meta = KEYS[id] || PENALTIES[id] || BREACHES[id];
  const el = document.createElement('span');
  // 红线单独一个类：它在对局中就要比失误更扎眼一点。踩线那一刻的反馈
  // 才教得会人，等到复盘才说，玩家已经忘了自己当时为什么那么讲。
  el.className = 'tag ' + (KEYS[id] ? 'key' : BREACHES[id] ? 'breach' : 'penalty');
  el.textContent = BREACHES[id] ? `合规红线 · ${BREACHES[id].name}` : (meta ? meta.name : id);
  return el;
}

/** 一轮没命中任何东西时，说点有用的。
 *
 * **原来这里只有一句「没使上劲」，八轮里七轮都是它。** 那等于告诉玩家
 * "你错了，但我不打算说错在哪"——而这恰恰是同类产品最被诟病的地方。
 * 我们手上其实有三条信号可以分辨，一条都没用过：
 *
 * · 判成扎根却没命中钥匙 → 他接住了对方的话，但停在那儿没往下问
 * · 一句话短到撑不起一轮 → 「嗯呢」「你先忙」，那不是劝，是应付
 * · 其余 → 确实没往三把钥匙上走
 *
 * 三句都指向下一步该怎么改，而「没使上劲」一句都不指。
 */
function missNote(t) {
  if (t.grounded) return `接住了${peerPronoun()}的话，但没往下问`;
  if ((t.utterance || '').replace(/\s/g, '').length <= 8) return '太短了，撑不起一轮';
  return '三把钥匙一把都没沾上';
}

function hitTags(t) {
  const out = t.hits.map(tag);
  // 说早了的「有据告知」不挂"扎根/未扎根"那一条：它根本没被当钥匙算过
  // （判分那边已经换成 bare_assertion），再说一句"效力打折"是在解释一件
  // 没发生的事，只会把玩家的注意力从真正的问题——时机——上引开。
  if (t.mistimedWarning) return out;
  if (t.hits.some((h) => KEYS[h])) {
    const g = document.createElement('span');
    g.className = 'tag';
    g.textContent = t.grounded ? '扎根' : '未扎根，效力打折';
    out.push(g);
  } else if (!t.hits.length) {
    const n = document.createElement('span');
    n.className = 'tag';
    n.textContent = missNote(t);
    out.push(n);
  }
  return out;
}

/** 剧情旁白。王老师在群里催的那一下走这个位置——
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

  // 这一轮作废时要原样退回去。服务端的令牌不会推进，界面也就一步都不能留。
  const prevRemaining = game.remaining;
  const mine = msgRow('me', utterance);
  thread.appendChild(mine);
  toBottom();
  const typing = typingRow();
  const queue = paced(typing);

  const rollback = () => {
    queue.rollback();
    mine.remove();
    game.remaining = prevRemaining;
    $('remaining').textContent = String(prevRemaining);
    const current = Math.min(game.maxRounds, Math.max(1, game.maxRounds - prevRemaining + 1));
    $('turnCurrent').textContent = String(current);
    $('roundFill').style.width = `${(current / game.maxRounds) * 100}%`;
    // 话还给他，省得重打一遍
    const input = $('say');
    if (!input.value) input.value = utterance;
  };

  let resp;
  try {
    resp = await fetch('api/game/turn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: game.token, utterance }),
    });
  } catch (e) {
    typing.remove();
    rollback();
    return failTurn('network');
  }

  if (!resp.ok || !resp.body) {
    typing.remove();
    rollback();
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
        $('turnCurrent').textContent = String(round);
        $('roundFill').style.width = `${(round / game.maxRounds) * 100}%`;
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

  if (failed) {
    rollback();
    return failTurn(failed);
  }

  if (score) {
    game.turns.push({
      round: round || game.turns.length + 1,
      utterance,
      reply: spoken.join(''),
      // 逐句留着，不只留拼好的那一整段：分享卡要挑的是**其中一句**
      lines: spoken.slice(),
      hits: score.hits,
      grounded: score.grounded,
      judgedMood: score.judged_mood,
      efficacy: score.efficacy,
      windowResult: score.window_result,
      windowOpened: score.window_opened,
      delta: score.delta,
      // 信任流失与蓄势池释放。复盘要用它们把账摊开——少了这两个数，
      // 「23 分 → +18 → 39」在玩家眼里就是一道算错的题
      drift: score.drift,
      released: score.released,
      pressure: score.pressure,
      trust: score.trust,
      before: game.trust,
      pool: score.pool,
      breached: score.breached,
      breaches: score.breaches,
      mistimedWarning: score.mistimed_warning,
      // **这一轮的分类没跑成，是按中性判的**（app/engine.py 的 `degraded`）。
      // 服务端一直在下发它，前端此前一次都没读过——后果不是"少一条提示"，
      // 是这一轮在复盘里**伪装成"玩家白打了一轮"**：hits 是空的，
      // 于是它照样进能力画像的分母、照样一格都不涨。
      // 网关抖了一下，账记在人头上。
      degraded: !!score.degraded,
    });
    game.trust = score.trust;
    paintMood(score.mood);
    // 判分卡不在对局中出现：标签与分数一律留到复盘。
    // 边打边给答案等于把攻略印在屏幕上——玩家两轮就学会照着清单刷分，
    // 从此不再读人。要读的东西只有一样：他说的话。
    //
    // **合规红线是这条规矩唯一的例外**，理由不是它更重要，是它性质不同：
    // 上面那条规矩防的是"泄漏怎么劝才有效"，而"投顾不能荐股"不是劝法，
    // 是这场练习的规则本身——说出来一分攻略都不漏。
    // 不给分数、不给标签，只说踩了哪条线。
    //
    // **措辞不能断言"本会话已存档"**（8-22 校准）：那是在给一个练习
    // 安上一条并不存在的监管义务。真实券商的展业留痕是真的，
    // 这个练习没有那套东西——说"真实展业中这句要留痕"既保住了
    // 全部教学分量，又不需要撒谎。
    if (score.breached) {
      const names = (score.hits || [])
        .filter((h) => h in BREACHES).map((h) => BREACHES[h].name);
      sysnote([`合规红线 · ${names.join(' / ')}`, '真实展业中这句要留痕'], true);
    }
    if (score.pressure) narrate('王老师又在群里催了一遍');
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

  // 这两种都没法接着打：令牌要么过期了，要么已经被消费掉。给一条出路，
  // 别让玩家对着一个再也发不出去的输入框反复试。
  if (code === 'invalid_state' || code === 'replayed') {
    $('composer').hidden = true;
    const again = document.createElement('button');
    again.textContent = '重开一局';
    again.onclick = () => location.reload();
    sysnote([code === 'replayed' ? '这一局没法接着打了' : '这一局放得太久了', again], true);
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
  el.querySelector('.receipt-to').textContent = PAYEE();
  el.querySelector('.receipt-foot').textContent = RECEIPT_FOOT[state];
  el.setAttribute('role', 'img');
  el.setAttribute('aria-label',
    `转账凭证截图：${text}，${PAYEE()}，${RECEIPT_FOOT[state]}`);
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
  const meta = endingMeta(kind);
  if (!meta.receipt) {
    // 被拒收之后聊天窗口显示的就是这一条。开局那句「对方是虚构客户」在这里
    // 合上了口：他还是你的客户，你还得对他负责，只是话再也递不进去了。
    const tip = document.createElement('p');
    tip.className = 'strangertip';
    tip.textContent = '消息已发出，但被对方拒收了。';
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

  // 代词与那句「他做了什么」都随场景走。原先这五句写死着"他"和"三十万"，
  // 周淑琴那一局读起来就是在讲另一个人的事
  const TA = peerPronoun();
  const meta = endingMeta(kind);
  if (kind === 'persuaded') parts.push(`你用了 ${game.turns.length} 轮把${TA}劝了回来。`);
  else if (kind === 'intercepted') parts.push(`${meta.savedCopy}。`);
  else if (kind === 'stalled') parts.push(`${TA}把这事推后了——你争到的是时间，不是${TA}的决定。`);
  else if (kind === 'blacklisted') parts.push(`第 ${game.turns.length} 轮，${TA}把你拉黑了——这条线断了，你再也看不到${TA}的动静。`);
  else parts.push(`${meta.savedCopy}。`);

  if (best && best.delta > 0) {
    // **"最有力的一句"这句不在这儿说了。** 上面「本局关键转折」整块讲的
    // 就是它：同一个轮次、同一句原话、同一个涨幅，紧挨着说两遍。
    // 这一段只留它独有的东西——追问窗口接没接住、最后一公里。
    //
    // **这句话以前是无条件说的**——只要没劝住就说"你没乘胜追击"，
    // 哪怕玩家每一次窗口都追上了。追问窗口这个机制本来就是为了让这句话
    // 变成真的（TECH-DESIGN §3.4），现在才真正接上线：错过了才说。
    const missed = game.turns.filter((x) => x.windowResult === 'missed');
    const caught = game.turns.filter((x) => x.windowResult === 'hit');
    if (missed.length) {
      // 报的是**开窗**那一轮，不是关窗那一轮：他晃起来是在开窗时，
      // 错过的那一轮只是口子合上的时刻。两个轮次差着两轮，说错了玩家会对不上号
      const opened = game.turns
        .filter((x) => x.windowOpened && x.round < missed[0].round)
        .pop();
      parts.push(
        `你差的不是方向——第 <em>${(opened || missed[0]).round}</em> 轮${TA}晃到了新的一档，` +
        `接下来两轮本来是机会，你没接住，第 ${missed[0].round} 轮${TA}重新硬了回去。`);
    } else if (caught.length && kind !== 'persuaded') {
      parts.push(
        `${TA}松动的那几次你都接住了（第 ${caught.map((x) => x.round).join('、')} 轮），` +
        `差的是最后一公里——越接近松口，同一句话推动${TA}的幅度越小。`);
    }
  } else {
    parts.push(`全场没有一句真正推动过${TA}。下一局试着先听懂${TA}在怕什么、在图什么，再往下问。`);
  }
  return parts.join('');
}

// ── 本机训练记录（跨局） ─────────────────────────────────────
//
// 单局复盘（paintKeyBars 等）回答"这一局你打得怎么样"；这里回答
// "打了这么多局，你是不是在变好"。**只存本机 localStorage**——没有账号
// 体系，不识别是谁，不能跨设备合并，因此也回答不了"团队里谁最常踩合规线"
// 那类问题（那需要服务端持有用户状态，是另一件事，见交接给项目所有者的
// 待定项）。localStorage 不可用（隐私模式、被禁用）时整块不出现，
// 复盘其余部分不受影响——和 Redis 不可用时 statsWrap 的处理是同一个原则。

const HISTORY_KEY = 'af_history_v1';
const HISTORY_CAP = 50; // 只是个防止无限增长的上限，不是产品意图

// 结局阶梯（CONTEXT.md「结局」）：四档由高到低，被拉黑不入档，
// 因此不参与"最好成绩"这个比较——把它硬塞进排名会把"出局"读成"垫底"。
const TIER_LABEL = {
  persuaded: '劝住', intercepted: '拦下', stalled: '拖住',
  transferred: '转账', blacklisted: '被拉黑',
};
const TIER_RANK = { persuaded: 4, intercepted: 3, stalled: 2, transferred: 1 };

function loadHistory() {
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
function recordGame(kind) {
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

function paintHistory(view, kind) {
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

function openReview() {
  const usedPenalties = Object.keys(PENALTIES).filter(
    (p) => game.turns.some((t) => t.hits.includes(p)));
  const best = game.turns.reduce(
    (a, b) => (b.delta > (a ? a.delta : -Infinity) ? b : a), null);
  const kind = game.ending ? game.ending.kind : 'transferred';
  const meta = endingMeta(kind);

  const view = document.createElement('section');
  view.className = 'review';
  view.setAttribute('role', 'dialog');
  view.setAttribute('aria-label', '复盘');
  view.innerHTML = `
    <div class="osbar"><span>14:56</span><span class="os-signals" aria-hidden="true"><i></i><i></i><i></i><b></b></span></div>
    <header class="appbar">
      <button class="brandmark review-mark" id="reviewBack" type="button" aria-label="返回对话">复</button>
      <strong>本局复盘</strong>
      <span class="quiet" id="reviewScene"></span>
    </header>
    <div class="review-body">
      <section class="summary ${kind}">
        <span class="result-kicker">本局结果 · <b class="tierpill"></b></span>
        <h2 class="result-title"></h2>
        <div class="savedamt num"></div>
        <div class="savedcap"></div>
        <div class="saved"></div>
      </section>

      <div class="scoreline">
        <div class="metric"><b class="num" id="sTrust"></b><span>最终信任</span></div>
        <div class="metric"><b class="num" id="sRounds"></b><span>使用轮次</span></div>
      </div>

      <p class="percentile" id="percentileLine">排行样本积累中 · 暂不显示百分位</p>

      <section class="turning-point" id="turningPoint">
        <b id="turningTitle"></b>
        <blockquote id="turningQuote"></blockquote>
        <p id="turningNote"></p>
      </section>

      <!-- 本局复盘：一句结论 + 三行可扫的要点（做对了 / 可改进 / 合规）。
           三行**从真实对局里算**，不按结局写死——写死的话，两个玩家用完全
           不同的打法拿到同一档结局，复盘会说一模一样的话，那是占位符不是复盘。
           判据见 reviewRows()。 -->
      <section class="result-review">
        <h3>本局复盘</h3>
        <p class="copy"></p>
        <div class="review-rows" id="reviewRows"></div>
      </section>

      <div class="result-actions">
        <details class="review-details" id="reviewDetails">
          <summary>查看逐轮证据</summary>
          <div class="evidence-content">
            <div class="panel">
              <canvas id="chart"></canvas>
              <p class="legend">一根蜡烛一轮，红涨绿跌。细横线是判分，实体端点是计入流失后的信任度。</p>
            </div>

            <div class="group" id="breachWrap" hidden>
              <div class="group-title">合规红线</div>
              <div class="panel" id="breachList"></div>
            </div>

            <div class="group">
              <div class="group-title" id="phoneTitle">这一局你没看见的</div>
              <div class="panel" id="phoneList"></div>
            </div>

            <div class="group" id="contrastWrap" hidden>
              <div class="group-title">同一句话，换个时候说</div>
              <div class="panel" id="contrastBox"></div>
            </div>

            <div class="group">
              <div class="group-title">关键方法 · 你这一局用得怎么样</div>
              <div class="panel" id="keyBars"></div>
            </div>

            <div class="group">
              <div class="group-title">逐轮 · 挣了多少 / 掉了多少 / 剩多少</div>
              <div class="panel" id="roundsList"></div>
            </div>

            <div class="group" id="penaltyWrap" hidden>
              <div class="group-title">踩过的坑</div>
              <div class="panel" id="penaltyList"></div>
            </div>

            <div class="group" id="statsWrap" hidden>
              <div class="group-title">别人打成什么样</div>
              <div class="panel" id="statsList"></div>
            </div>

            <div class="group" id="historyWrap" hidden>
              <div class="group-title">这台设备上的训练记录</div>
              <div class="panel statstrip" id="historyStrip"></div>
              <div class="panel" id="historyList"></div>
              <div class="panel keyrow" id="historyWeak" hidden>
                <div class="keyhead"><span class="keyname"></span><span class="keynum"></span></div>
                <div class="keynote"></div>
              </div>
            </div>

            <!-- 这一局练的只是"怎么开口"。现实里把话说通之后还有一串动作，
                 而这套判分闭集里一个都没有（七把钥匙全是问法）。
                 不列出来，玩家会以为劝住了就完事了——那是这个作品最容易
                 教错的一件事，而它只要一张清单就能说清。
                 **不计分、不参与任何统计**：它是"接下来还要做什么"，不是成绩。 -->
            <div class="group">
              <div class="group-title">话说通了，现实里还差这几步</div>
              <div class="panel">
                <ol class="disposal">
                  <li><b>先把这一笔停下</b><span>让客户当场取消转账或撤回；已提交的联系银行尝试拦截。</span></li>
                  <li><b>核验收款方</b><span>对公户还是个人卡、户名对不对得上他说的那家机构。</span></li>
                  <li><b>拨 96110 / 110</b><span>陪着他打，别让他挂了电话自己再想。</span></li>
                  <li><b>在系统里留痕并上报</b><span>疑似诈骗按本机构流程报备，别只留在聊天记录里。</span></li>
                  <li><b>约下一次回访</b><span>骗子还会再找他。这一通电话不是终点。</span></li>
                </ol>
                <p class="empty">这几步本局不计分，也不该由一次对话代替。真实处置流程以你所在机构的规定为准。</p>
              </div>
            </div>

            <div class="actions"><button id="makeCard">生成分享卡</button></div>
            <div id="cardWrap"></div>
            <p class="howscored">上面每一分都是<b>程序按规则表算的，不是模型打的</b>：同一把钥匙在客户不同的情绪档位上值不同的分，这张规则表是纯函数、可以离线重跑。<b>但"命中了哪一把"仍由模型判定</b>，那一步不是确定性的——所以别把这里的分当成一个精确刻度，它是画像，不是成绩单。</p>
          </div>
        </details>
        <button class="restart-action" id="restart" type="button">开始一位新客户</button>
      </div>
    </div>`;

  document.querySelector('.app-shell').appendChild(view);
  // 四色语义（设计稿的 data-tone）：绿只给明确劝住，金＝争取到时间或减少了
  // 损失，锈红＝资金损失或联系中断，拖住给中性灰——钱没动但风险一点没解除，
  // 用绿会把"还没输"说成"赢了"。**绿不做装饰色**：它在这一屏只有一个意思，
  // 滥用一次，下次玩家就不信它了。一屏一个 --accent，百分位块直接吃它。
  view.dataset.tone = TONES[kind] || 'plain';
  const result = resultAmount(kind);
  view.querySelector('#reviewScene').textContent = SCENE ? SCENE.name : '风险劝阻';
  view.querySelector('.summary .tierpill').textContent = meta.tier;
  view.querySelector('.summary .result-title').textContent = meta.title;
  view.querySelector('.summary .savedamt').textContent = result.value;
  view.querySelector('.summary .savedcap').textContent = result.label;
  view.querySelector('.summary .saved').textContent = meta.savedCopy;
  view.querySelector('.result-review .copy').innerHTML = verdictCopy();
  paintReviewRows(view);

  const turning = view.querySelector('#turningPoint');
  if (best && best.delta > 0) {
    turning.querySelector('#turningTitle').textContent =
      `真正改变${peerPronoun()}的是第 ${best.round} 轮`;
    turning.querySelector('#turningQuote').textContent = `“${best.utterance}”`;
    turning.querySelector('#turningNote').textContent =
      `第 ${best.round} 轮让信任上升 ${best.delta} 分，这是本局最有力的一句。`;
  } else {
    turning.querySelector('#turningTitle').textContent = '本局尚未出现关键转折';
    turning.querySelector('#turningQuote').textContent = '这一局没有一句真正推动客户。';
    turning.querySelector('#turningNote').textContent =
      '下一局先确认客户在怕什么、相信什么，再尝试给出判断。';
  }

  if (kind === 'blacklisted') {
    const rank = view.querySelector('#percentileLine');
    rank.hidden = false;
    rank.classList.add('unranked');
    rank.textContent = '不参与排行 · 联系中断属于提前出局';
  }

  view.querySelector('#sTrust').textContent = String(game.trust);
  view.querySelector('#sRounds').textContent = String(game.turns.length);

  paintBreaches(view);
  paintPhone(view);

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
    hitTags(t).forEach((x) => tags.appendChild(x));
    body.append(said, tags);
    const when = timingNote(t);
    if (when) body.append(when);
    const win = windowNote(t);
    if (win) body.append(win);
    // **这一轮没判成。** 必须说出来，而且必须说清是我们的问题：
    // 不说的话，这一行看起来就是"你说了句什么也没命中的话"——
    // 一次网关抖动被永久记成了玩家的失误，而且他连申辩的依据都没有。
    // 上面 scoredTurns() 已经把它踢出了所有比率的分母，这里补上告知。
    if (t.degraded) {
      const bad = document.createElement('div');
      bad.className = 'window';
      bad.textContent = '这一轮没能判分（系统原因），已不计入上面的能力画像';
      body.append(bad);
    }
    // 催单那一轮多掉 3 分。不写出来，玩家只会看到流失那一列忽然从 -2 变成 -5，
    // 而 title 提示在手机上根本摸不到
    if (t.pressure) {
      const push = document.createElement('div');
      push.className = 'window';
      push.textContent = '王老师这一轮又在群里催了一遍 · 多掉 3 分';
      body.append(push);
    }

    row.append(no, body, scoreLedger(t));
    list.appendChild(row);
  });

  paintContrast(view);
  paintKeyBars(view);

  if (usedPenalties.length) {
    view.querySelector('#penaltyWrap').hidden = false;
    const box = view.querySelector('#penaltyList');
    usedPenalties.forEach((p) => box.appendChild(tipCard(PENALTIES[p])));
  }

  paintStats(view, kind);
  paintHistory(view, kind);

  view.querySelector('#restart').onclick = startNewClient;
  view.querySelector('#makeCard').onclick = () => makeCard(view);
  view.querySelector('#reviewBack').onclick = () => view.remove();
  view.querySelector('#reviewDetails').addEventListener('toggle', (event) => {
    if (event.currentTarget.open) paintChart(view);
  }, { once: true });
}

/** 合规红线那一节。
 *
 * **这是全作品唯一一处与输赢无关的判定。** 其余每一个数字都在回答
 * "你有没有劝住他"；这一节回答的是另一个问题——"你这么劝，自己有没有事"。
 * 两个问题的答案可以同时是"很好"和"很糟"，而那正是它要教的东西。
 *
 * 逐条列出踩线的那一轮和原话：合规这件事上，"你说过这句话"本身就是证据，
 * 泛泛说一句"注意合规"没有任何用。
 */
/** 本局复盘三行：做对了 / 可改进 / 合规。
 *
 * **设计稿里这三行是按结局写死的**（每个客户 × 每种结局一套文案）。
 * 照抄会出一个问题：两个玩家用完全不同的打法拿到同一档结局，复盘会对他们
 * 说一模一样的话——那是占位符，不是复盘。所以这里全部从这一局的真实数据算：
 *
 *   做对了 —— 真实挣分最多的那把钥匙（一把都没挣到就说实话）
 *   可改进 —— 优先说踩过的坑；没踩坑才说这一局压根没用过的那几把
 *   合规   —— 直接读 `breachTurns()`，与那张合规红线卡同源，不另算一套口径
 */
function reviewRows() {
  const gains = {};
  game.turns.forEach((t) => t.hits.forEach((h) => {
    if (KEYS[h]) gains[h] = (gains[h] || 0) + Math.max(0, t.delta);
  }));
  const bestKey = Object.keys(gains)
    .filter((k) => gains[k] > 0)
    .sort((a, b) => gains[b] - gains[a])[0];

  const didRight = bestKey
    ? `${KEYS[bestKey].name}用得最见效，这一局靠它挣了 ${gains[bestKey]} 分`
    : '识别到了资产异动，并且开口问了';

  const penalties = Object.keys(PENALTIES).filter(
    (p) => game.turns.some((t) => t.hits.includes(p)));
  const unused = Object.keys(KEYS).filter(
    (k) => !game.turns.some((t) => t.hits.includes(k)));
  const canImprove = penalties.length
    ? `${penalties.map((p) => PENALTIES[p].name).join('、')}顶高了${peerPronoun()}的防备`
    : unused.length
      ? `这一局没用过${unused.slice(0, 2).map((k) => KEYS[k].name).join('、')}`
      : `七把钥匙都用到了，下一局试着更早读准${peerPronoun()}的档位`;

  const breaches = breachTurns();
  const compliance = breaches.length
    ? `踩了 ${breaches.length} 次执业红线，这在现实里是要被问话的`
    : '未触碰敏感信息红线';

  return [
    ['做对了', didRight, false],
    ['可改进', canImprove, false],
    ['合规', compliance, breaches.length > 0],
  ];
}

function paintReviewRows(view) {
  const box = view.querySelector('#reviewRows');
  if (!box) return;
  reviewRows().forEach(([label, text, bad]) => {
    const row = document.createElement('div');
    row.className = 'review-row';
    const dt = document.createElement('span');
    dt.textContent = label;
    const dd = document.createElement('b');
    if (bad) dd.className = 'redline';
    dd.textContent = text;
    row.append(dt, dd);
    box.appendChild(row);
  });
}

function paintBreaches(view) {
  const turns = breachTurns();
  if (!turns.length) return;

  view.querySelector('#breachWrap').hidden = false;
  const box = view.querySelector('#breachList');

  const lead = document.createElement('p');
  lead.className = 'breach-lead';
  const kinds = new Set();
  turns.forEach((t) => t.hits.forEach((h) => h in BREACHES && kinds.add(h)));
  // 最后一句原来是「本会话按监管要求存档，开局那条提示不是布景」。
  // 两处都不成立：这个练习没有任何监管存档义务，而开局那条提示 8-22 已改。
  // 换成一句关于**真实展业**的陈述——教学分量一分没少，断言没了。
  lead.innerHTML =
    `这一局你有 <b>${turns.length}</b> 轮踩到了执业红线。` +
    `<br>这一节和你劝没劝住他无关 —— <b>这样的对话记录，合规那边看到是要问话的</b>。` +
    `真实展业中，投顾与客户的沟通是要留痕的。`;
  box.appendChild(lead);

  turns.forEach((t) => {
    const row = document.createElement('div');
    row.className = 'breachrow';

    const head = document.createElement('div');
    head.className = 'breachhead';
    t.hits.filter((h) => h in BREACHES).forEach((h) => {
      const b = document.createElement('span');
      b.className = 'breachname';
      b.textContent = BREACHES[h].name;
      head.appendChild(b);
    });
    const no = document.createElement('span');
    no.className = 'breachno';
    no.textContent = `第 ${t.round} 轮`;
    head.appendChild(no);

    const said = document.createElement('div');
    said.className = 'breachsaid';
    said.textContent = `你说：${t.utterance}`;

    row.append(head, said);
    t.hits.filter((h) => h in BREACHES).forEach((h) => {
      const why = document.createElement('div');
      why.className = 'breachwhy';
      why.textContent = BREACHES[h].tip;
      row.appendChild(why);
    });
    box.appendChild(row);
  });
}

// ── 揭晓：老陈的手机 ────────────────────────────────────────
//
// 这五条会话原本是首页那一屏。移过来的理由写在 index.html 的注释里，
// 一句话说完：**首页把这一局要挖的东西全给了，而挖它就是玩法。**
//
// 判据是"**他**说没说过"，不是"你猜没猜到"——所以匹配跑在老陈的台词上。
// 这与扎根的判据同源（引用的是他说过的话），也更诚实：玩家从别处知道
// 老陈有个女儿不算本事，把他问到主动提起小雨才算。
//
// 招行那条单独标"这一条你有"：系统推给李经理的资产异动预警就是它的另一面。
// 它在这张清单上的作用是让"你开局只有这一条"看得见。
// 揭晓清单**随场景下发**（app/scenario.py 的 PhoneRow）。它原先是写死在这里的
// 五条老陈的会话——第二个场景一来，那五条就成了另一个人的手机。
//
// `test` 是正则源码字符串，在这儿编译。判据仍然是"**他**说没说过"，
// 匹配跑在劝阻对象的台词上，与扎根同源。
function phoneRows() {
  return (SCENE ? SCENE.phone : []).map((r) => ({
    cls: r.avatar,
    text: r.initial,
    name: r.name,
    time: r.time,
    line: r.line,
    clue: r.clue,
    own: r.own,
    test: r.test ? new RegExp(r.test) : null,
  }));
}

/** 他这一局说过的话，按时间序，不做任何过滤——掐掉一个字都可能让
 *  某条线索误判成"他没提"。
 */
function hisSpeech() {
  const out = [{ round: 0, text: game.opening || '' }];
  game.turns.forEach((t) =>
    out.push({ round: t.round, text: t.reply || (t.lines || []).join('') }));
  ((game.ending && game.ending.lines) || []).forEach((line) =>
    out.push({ round: null, text: line }));
  return out.filter((x) => x.text);
}

/** 揭晓老陈的手机：哪几条他跟你说了，哪几条到最后你也不知道。 */
function paintPhone(view) {
  const box = view.querySelector('#phoneList');
  const speech = hisSpeech();
  const rows = phoneRows();
  const diggable = rows.filter((x) => !x.own);
  const TA = peerPronoun();
  // 标题也随场景走。原先写死「老陈的手机」，周淑琴那一局照样这么印
  view.querySelector('#phoneTitle').textContent =
    `这一局你没看见的 · ${SCENE ? SCENE.client.name : '客户'}的手机`;
  let got = 0;

  const lead = document.createElement('p');
  lead.className = 'phone-lead';
  box.appendChild(lead);

  rows.forEach((item) => {
    const found = item.own
      ? null
      : speech.find((s) => item.test.test(s.text));
    if (found) got += 1;

    const row = document.createElement('li');
    row.className = 'chatrow' + (!item.own && !found ? ' missed' : '');

    const av = document.createElement('span');
    av.className = 'avatar ' + item.cls;
    av.setAttribute('aria-hidden', 'true');
    av.textContent = item.text;
    // 群头像是四格拼的，没有文字
    if (item.cls === 'av-group') av.innerHTML = '<i></i><i></i><i></i><i></i>';

    const state = document.createElement('span');
    if (item.own) {
      state.className = 'phone-state own';
      state.textContent = '你已有';
    } else if (found) {
      state.className = 'phone-state yes';
      // 开场白算第 0 轮，结局台词没有轮次——都得说人话，不能印出「第 0 轮」
      state.textContent =
        found.round === 0 ? `${TA}开口就说了`
          : found.round === null ? '最后才说'
            : `${TA}第 ${found.round} 轮说了`;
    } else {
      state.className = 'phone-state no';
      state.textContent = `${TA}没提`;
    }

    const main = document.createElement('span');
    main.className = 'row-main';
    main.innerHTML = `
      <span class="row-top">
        <span class="row-name"></span>
        <span class="row-time"></span>
      </span>
      <span class="row-bottom">
        <span class="row-preview"></span>
      </span>
      <span class="phone-clue"></span>`;
    main.querySelector('.row-name').textContent = item.name;
    main.querySelector('.row-time').textContent = item.time;
    main.querySelector('.row-preview').textContent = item.line;
    main.querySelector('.phone-clue').textContent = item.clue;
    main.querySelector('.row-bottom').appendChild(state);

    row.append(av, main);
    box.appendChild(row);
  });

  // 数字之外还要有一句判断，否则「4 条里问到 1 条」玩家不知道算好算差
  const verdict =
    got === diggable.length ? `${TA}几乎什么都跟你说了 —— 这一局你是真把${TA}问开了。`
      // 不能写死"十二轮下来"：被拉黑与提前结束的局根本没打满
      : got === 0 ? `一条都没有 —— 打完这一局，你对${TA}的了解和开局时一样多。`
        : got * 2 >= diggable.length ? `问出一半以上，${TA}对你是有话说的。`
          : '大部分到最后你也不知道 —— 而不知道这些，你就只能泛泛地劝。';
  lead.innerHTML =
    `开局你手上只有账户那一侧的一条预警。${TA}手机上还有这些，` +
    `<b>${diggable.length}</b> 条里${TA}跟你说到了 <b class="got">${got}</b> 条。<br>${verdict}`;
}

/** 三把钥匙的维度条。
 *
 * **这一节是补一条真实的产品差距。** 同类的 AI 陪练产品人人都有"维度评分"，
 * 而我们原先只有一张「没用上的钥匙」清单——判分引擎明明按三把钥匙 × 四个
 * 情绪档位算了一整局，玩家却看不到自己在每一把上站在哪儿。
 *
 * 每一行给三样东西，都从这一局的真实数据里算，不编：
 *   · 用了几次（钝化就是从这儿来的：同一把钥匙用第三次只剩一半效力）
 *   · 这把钥匙一共挣了多少分
 *   · 时机对不对（效力倍率的均值——**这是全作品唯一一处竞品没有的判据**）
 *
 * 没用过的那几把不留白，给出它的一句话说明——那正是下一局该试的东西。
 */
/** 「同一句话，换个时候说」。
 *
 *  **这是全作品唯一一条竞品没有的判据第一次被摆到玩家眼前。**
 *  金融 AI 陪练判"回答准确度、语速、音量"，销售 roleplay 判"方法论有没有
 *  照做"，没有一家判"你这一招用得是不是时候"——而效力矩阵判的正是这个。
 *  在这个块之前，它只出现在两个地方：逐轮里一闪而过的 `1.3×`，
 *  以及技术文档。**玩家自己打一局是看不见它的**，评审也一样。
 *
 *  矩阵随结局事件下发（app/engine.py），不在前端抄一份（踩过的坑 7）：
 *  调参数时蒙特卡洛会重跑，抄一份的话页面上这个数不会跟着动。
 *
 *  挑哪一轮来讲，按"教学价值"排，不按分数排：
 *    1. 说早了的「有据告知」——同一句话早说是失误、晚说是钥匙，
 *       这是本条判据的**极端形态**，有就一定讲它
 *    2. 否则挑"实际效力与这一把最高效力差得最多"的那一轮——
 *       他做对了动作、只是挑错了时候，那正是这一节要教的
 *  两样都没有（比如一把钥匙都没用上），整块不出现——不留一个空壳。 */
function paintContrast(view) {
  const box = view.querySelector('#contrastBox');
  const eff = game.ending && game.ending.efficacy;
  if (!eff || !box) return;

  const named = (m) => MOODS[m] || m;
  const rowOf = (k) => eff[k] || null;
  // 一把钥匙在四档里的最高与最低。用来说"同一句话差多少倍"
  const peak = (row) => Object.entries(row).sort((a, b) => b[1] - a[1])[0];
  const floor = (row) => Object.entries(row).sort((a, b) => a[1] - b[1])[0];

  let head = '';
  let body = '';
  let quote = '';

  const mistimed = scoredTurns().find((t) => t.mistimedWarning);
  if (mistimed && rowOf('informed_warning')) {
    const row = rowOf('informed_warning');
    const [bestMood, bestVal] = peak(row);
    quote = mistimed.utterance;
    head = `第 ${mistimed.round} 轮 · 你给了依据，也下了判断`;
    body =
      `他当时${named(mistimed.judgedMood)}，这句话算的是<b>空口断言</b>——`
      + `跟他女儿昨天说的那四个字落在同一个地方。`
      + `<br>同一句话，等他${named(bestMood)}再说，它是这一局分值最高的一把（${bestVal}×）。`
      + `<br><b>不是这句话错了，是时候错了。</b>`;
  } else {
    // 找"动作对、时候不对"差得最远的那一轮
    let worst = null;
    scoredTurns().forEach((t) => {
      if (t.efficacy == null) return;
      const k = (t.hits || []).find((h) => rowOf(h));
      if (!k) return;
      const [bestMood, bestVal] = peak(rowOf(k));
      const gap = bestVal - t.efficacy;
      if (!worst || gap > worst.gap) worst = { t, k, bestMood, bestVal, gap };
    });
    if (!worst || worst.gap <= 0.15) return;   // 差得不明显就不硬讲
    const { t, k, bestMood, bestVal } = worst;
    quote = t.utterance;
    head = `第 ${t.round} 轮 · ${KEYS[k] ? KEYS[k].name : k}`;
    body =
      `他当时${named(t.judgedMood)}，这一招值 ${t.efficacy}×。`
      + `<br>同一句话，等他${named(bestMood)}再说，值 ${bestVal}×。`
      + `<br><b>动作是对的，差的是时候。</b>`;
  }

  if (!head) return;

  const h = document.createElement('div');
  h.className = 'contrast-head';
  h.textContent = head;
  box.appendChild(h);

  if (quote) {
    const q = document.createElement('div');
    q.className = 'contrast-quote';
    q.textContent = quote;
    box.appendChild(q);
  }

  const p = document.createElement('p');
  p.className = 'contrast-body';
  p.innerHTML = body;
  box.appendChild(p);

  // 一句话把这个块为什么存在说清楚。竞品判"说得标不标准"，我们判"用得是不是
  // 时候"——这句是全作品的论点，值得在它唯一被看见的地方写出来
  const foot = document.createElement('p');
  foot.className = 'empty';
  foot.textContent =
    '这一局判的从来不是你说得标不标准，是你用得是不是时候。'
    + '同一把钥匙，在他四种情绪下值的分不一样——换个客户，这张表还会翻过来。';
  box.appendChild(foot);

  view.querySelector('#contrastWrap').hidden = false;
}

function paintKeyBars(view) {
  const box = view.querySelector('#keyBars');
  const scored = scoredTurns();
  const rows = Object.keys(KEYS).map((k) => {
    const turns = scored.filter((t) => t.hits.includes(k));
    const gained = turns.reduce((s, t) => s + Math.max(0, t.delta), 0);
    const effs = turns.map((t) => t.efficacy).filter((e) => e != null);
    return {
      k,
      used: turns.length,
      gained,
      eff: effs.length ? effs.reduce((a, b) => a + b, 0) / effs.length : null,
      rounds: turns.map((t) => t.round),
    };
  });
  // 挣得多的排前面。没用过的沉底——它们是"下一局试试这个"，不是成绩
  rows.sort((a, b) => b.gained - a.gained || b.used - a.used);

  const top = Math.max(1, ...rows.map((r) => r.gained));

  rows.forEach((r) => {
    const meta = KEYS[r.k];
    const row = document.createElement('div');
    row.className = 'keyrow' + (r.used ? '' : ' unused');

    const head = document.createElement('div');
    head.className = 'keyhead';
    const name = document.createElement('span');
    name.className = 'keyname';
    name.textContent = meta.name;
    const num = document.createElement('span');
    num.className = 'keynum num';
    num.textContent = r.used ? `+${r.gained}` : '没用过';
    head.append(name, num);

    const bar = document.createElement('div');
    bar.className = 'keybar';
    const fill = document.createElement('i');
    // 条长按"这一局挣得最多的那把"归一。绝对分值没有天花板可言，
    // 拿一个想象出来的满分去除，条会长期趴在左边，什么也说明不了
    fill.style.width = r.gained ? Math.max(6, (r.gained / top) * 100) + '%' : '0%';
    bar.appendChild(fill);

    const note = document.createElement('div');
    note.className = 'keynote';
    if (!r.used) {
      note.textContent = meta.tip;
    } else {
      const parts = [`第 ${r.rounds.join('、')} 轮用了 ${r.used} 次`];
      if (r.eff != null) {
        const e = r.eff.toFixed(1);
        parts.push(
          r.eff >= 1.2 ? `平均 ${e}× · 时机抓得准`
            : r.eff < 0.7 ? `平均 ${e}× · 用早了，这一招得等他晃起来`
            : `平均 ${e}×`);
      }
      if (r.used >= 3) parts.push('用到第三次效力只剩一半');
      note.textContent = parts.join(' · ');
    }

    row.append(head, bar, note);
    box.appendChild(row);
  });
}

/** 一轮的账，摊开写。
 *
 * **原来这里只有两个数：判分和结果。** 于是复盘上是这样的：
 * 第 3 轮 23 分 → 第 4 轮 +18 → 39。玩家会去算 23+18=41，对不上，
 * 然后合理地怀疑判分是不是错了。差的那 2 分是每轮无条件的信任流失，
 * 而它在界面上一个字都没有——机制建了三个月，玩家一次都没见过它。
 *
 * 现在三段都写出来：挣了多少 / 掉了多少 / 剩多少。掉的那一列还要说明白
 * 为什么是 5 不是 2（王老师催单）——那正是「他背后有人在往回拉」这件事
 * 唯一一次在数字上现身。
 */
function scoreLedger(t) {
  const box = document.createElement('div');
  box.className = 'ledger num';

  const gain = document.createElement('span');
  gain.className = 'gain' + (t.delta > 0 ? ' up' : t.delta < 0 ? ' down' : '');
  gain.textContent = t.delta > 0 ? `+${t.delta}` : String(t.delta);
  box.appendChild(gain);

  // drift 是后加的字段。老令牌里没有它，别让一局旧对局在复盘上崩掉
  if (typeof t.drift === 'number' && t.drift) {
    const loss = document.createElement('span');
    loss.className = 'loss';
    loss.textContent = String(t.drift);
    loss.title = t.pressure ? '每轮的信任流失，加上王老师这一轮又催了一遍' : '每轮的信任流失';
    box.appendChild(loss);
  }
  if (t.released) {
    const pool = document.createElement('span');
    pool.className = 'pool';
    pool.textContent = `+${t.released}`;
    pool.title = '开局封顶时存进蓄势池的分，这一轮释放出来了';
    box.appendChild(pool);
  }

  const after = document.createElement('span');
  after.className = 'after';
  after.textContent = String(t.trust);
  box.appendChild(after);
  return box;
}

/** 「别人打成什么样」。
 *
 * `app/stats.py` 与 `/api/stats` 早就写好了，前端一次都没调用过——同类产品
 * 公认的四个失败模式里，"量的是对话，不是能不能上岗"就漏在这儿。一个人打完
 * 只知道自己这一局，接上之后才知道自己站在哪：你这一档占多少、三把钥匙里
 * 哪一把是大家都想不起来用的。
 *
 * **全程是旁路**：Redis 没配、接口挂了、还没人玩过，这一节整块不出现，
 * 复盘的其余部分一个字都不受影响。绝不让一个纯展示功能拖累最后一屏。
 */
// 后端 `app/stats.py` 的 `_trust_bucket`：5 分一档、20 个桶，下标 = trust // 5。
// 复盘定的门槛：样本（桶内计数之和）不满 20 局不显示——数据太少时报一个
// "超过 100% 的人"没有意义，不如不说，跟这一节整体"没数据就不出现"是同一条原则。
const TRUST_SAMPLE_MIN = 20;

/** 分布是分桶存的，不是每一局的原始值，百分位因此是个近似值：
 *  桶外的直接算"被我超过"，桶内按信任度在这 5 分区间里的相对位置插值——
 *  不然数字会卡在 5 分一档的台阶上，一眼就看得出是硬凑的。 */
function trustPercentile(buckets, trust) {
  if (!Array.isArray(buckets) || !buckets.length) return null;
  const total = buckets.reduce((a, b) => a + b, 0);
  if (total < TRUST_SAMPLE_MIN) return null;
  const mine = Math.max(0, Math.min(buckets.length - 1, Math.floor(trust / 5)));
  const below = buckets.slice(0, mine).reduce((a, b) => a + b, 0);
  const within = buckets[mine] || 0;
  const pos = within ? Math.max(0, Math.min(1, (trust - mine * 5) / 5)) : 0;
  return Math.round(((below + within * pos) / total) * 100);
}

/** 百分位这句话，跟 verdictCopy 是同一套嘴——具体、说人话、不打鸡血。
 *
 * **原来的写法是"这一局的信任度超过了已有记录里 X%"**：不管 X 是 95 还是 5，
 * 都是同一句模板换个数字，是典型的"仪表盘播报腔"。分数低的时候尤其显得假——
 * 一个 15% 配一句语气跟 95% 一模一样的话，像是没看懂自己在说什么。
 *
 * 分数不同，值得说的话也不同：高分是真值得夸的一手；低分不回避那个数，
 * 但接一句具体能改的东西，跟 verdictCopy 低分那句"下一局试着先听懂他在怕
 * 什么"是同一个路数——情绪价值不是把烂分数说成好分数，是把冷冰冰的排名
 * 换成一句听得出是在跟你说话的话。
 */
function percentileCopy(pct) {
  return `高于同场景 ${pct}% 的已完成对局`;
}

/** 百分位配色跟着分数走，不是每次都用那罐"值得庆祝"的绿——
 *  15% 配一个和 95% 一样鲜亮的绿底，正是看着"怪"的地方。
 *  三色沿用复盘正文其余地方的用法：够亮眼才给品牌绿，其余一律中性灰。 */
function percentileTier(pct) {
  return pct >= 60 ? 'good' : 'plain';
}

/** percentileCopy 的简短版，给分享卡用——卡片宽度固定，长版那句带建议的话
 *  放不下一行，canvas 又不像 CSS 那样会自动折行。语气分级跟长版是同一套。 */
function percentileHeadline(pct) {
  if (pct >= 85) return `比 ${pct}% 打过的人都高`;
  if (pct >= 60) return `超过了 ${pct}% 的人`;
  if (pct >= 35) return `超过了 ${pct}% 的人，还有空间`;
  if (pct >= 10) return `超过了 ${pct}% 的人，才刚起步`;
  return `超过了 ${pct}% 的人，这局是真难`;
}

async function paintStats(view, kind) {
  // 被拉黑是提前出局，不属于四档完整对局，不拿 0 分和完成对局比较。
  if (kind === 'blacklisted') return;
  let data;
  try {
    // **必须带 sid**：信任度分布按场景分开存（app/stats.py `key_trust`）。
    // 四个场景难度并不一样（balance_sim 实测 expert 胜率 45.8%~54.0%），
    // 混在一起比，量出来的是"你抽到的场景是难是易"，不是你打得好不好。
    // 不带这个参数会读到空的默认桶，百分位于是永远不显示——**静默失效**，
    // 页面上看不出任何异常，所以这行不能省。
    const sid = encodeURIComponent(SCENE ? SCENE.id : '');
    data = await (await fetch(`api/stats?sid=${sid}`)).json();
  } catch (e) {
    return;
  }
  if (!data || !data.available) return;

  // 百分位单独判定，不跟下面 `!data.turns` 的早退共用一个门槛——
  // 它现在挂在结算卡那块里，不属于「别人打成什么样」这一节，
  // 后者没数据不该连累前者也不出现。
  const pct = trustPercentile(data.trust_buckets, game.trust);
  if (pct != null) {
    game._percentile = pct;
    const line = view.querySelector('#percentileLine');
    line.hidden = false;
    line.textContent = percentileCopy(pct);
    // 底色不在这儿判：它跟着整屏的 data-tone 走（style.css 的 .review[data-tone]），
    // 与上面那个大数同色。一屏一个语义色，玩家不用学第二套规则。
  }

  if (!data.turns) return;

  const rows = [];
  // 分母只数真正判成了的轮次（见 scoredTurns）——降级轮的 hits 是空的，
  // 留在分母里等于让网关抖动去稀释玩家的命中率
  const scored = scoredTurns();
  const mine = scored.length;

  const share = data.endings && data.endings[kind];
  if (share && share.share) {
    rows.push([
      `你落在「${endingMeta(kind).title}」`,
      `${Math.round(share.share * 100)}% 的人也停在这一档`,
    ]);
  }

  // 分母两边都是"轮"：全局用总轮数，你这局用你打过的轮数。
  // 换成局数会得出大于 1 的"命中率"——一局里同一把钥匙可以用很多次。
  Object.keys(KEYS).forEach((k) => {
    const g = data.keys && data.keys[k];
    if (!g) return;
    const 我的 = scored.filter((t) => t.hits.includes(k)).length;
    rows.push([
      KEYS[k].name,
      `大家 ${Math.round(g.rate * 100)}% 的发言用到 · 你 ${mine ? Math.round((我的 / mine) * 100) : 0}%`,
    ]);
  });

  // 小雨那条铺垫不在这儿重复说：上面「踩过的坑」里的空口断言卡片已经写了
  // 「这四个字他这三个月听了无数遍」。这一行只负责给一个数。
  const 空口 = data.keys && data.keys.bare_assertion;
  if (空口 && 空口.rate) {
    rows.push([
      '空口说“这是诈骗”',
      `${Math.round(空口.rate * 100)}% 的发言还在这么劝`,
    ]);
  }

  if (!rows.length) return;

  const box = view.querySelector('#statsList');
  rows.forEach(([name, note]) => {
    const row = document.createElement('div');
    row.className = 'statrow';
    const n = document.createElement('div');
    n.className = 'statname';
    n.textContent = name;
    const v = document.createElement('div');
    v.className = 'statnote';
    v.textContent = note;
    row.append(n, v);
    box.appendChild(row);
  });

  const foot = document.createElement('p');
  foot.className = 'empty';
  foot.textContent = `统计自 ${data.games} 局、${data.turns} 轮对话`;
  box.appendChild(foot);

  view.querySelector('#statsWrap').hidden = false;
}

/** 时机那一行。**这是全作品唯一一处竞品没有的判据**——金融 AI 陪练判
 *  "回答准确度、语速、音量"，销售 roleplay 判"方法论有没有照做"，
 *  没有一家判"你这一招用得是不是时候"。而效力矩阵判的正是这个：
 *  同一把钥匙，他戒备时用和他动摇时用，差四倍多。
 *
 *  倍率由服务端下发（判分参数只此一份，前端不抄那张表）。
 */
function timingNote(t) {
  // 说早了的「有据告知」优先说这一句。判分那边已经把它换成了 bare_assertion，
  // 玩家只看到一个"空口断言"标签会完全对不上号——**他明明给了依据**。
  // 错的不是那句话，是时候，而这正是全作品唯一在判的东西的极端形态：
  // 同一句话，早说是失误，晚说是钥匙。
  if (t.mistimedWarning) {
    const el = document.createElement('div');
    el.className = 'timing bad';
    el.textContent =
      `他当时${MOODS[t.judgedMood] || t.judgedMood} · `
      + '这句话本身没问题，你给了依据 —— 但他还没到听得进去的时候，'
      + '这时候说，跟「这是诈骗」四个字在他耳朵里是一样的';
    return el;
  }
  if (t.efficacy == null) return null;
  const mood = MOODS[t.judgedMood] || t.judgedMood;
  const el = document.createElement('div');
  el.className = 'timing' + (t.efficacy >= 1.2 ? ' good' : t.efficacy < 0.7 ? ' bad' : '');
  const verdict = t.efficacy >= 1.2 ? ' 正是时候'
    : t.efficacy < 0.7 ? ' 用早了，这一招得等他晃起来' : '';
  el.textContent = `他当时${mood} · 这一招值 ${t.efficacy}×${verdict}`;
  return el;
}

// 追问窗口那一行。同类产品公认的一个洞是"评分捕捉不到回避行为"——
// 受训者在安全话题上过度展开，一碰到真正的痛点就退回安全区，而评分只看
// 话术完整度，反而给了正面激励。追问窗口捕捉的正是它：他晃起来的那两轮
// 你有没有跟上。以前这条只在引擎里算，玩家一眼都看不到。
const WINDOW_NOTE = {
  hit: { text: '他正晃着，你接住了 · 这一招额外加成', cls: 'good' },
  missed: { text: '两轮的口子空掉了，他重新硬了回去 · 扣 6 分', cls: 'bad' },
  open: { text: '口子还开着，还剩一轮', cls: '' },
};

function windowNote(t) {
  const parts = [];
  const w = WINDOW_NOTE[t.windowResult];
  if (w) parts.push([w.text, w.cls]);
  if (t.windowOpened) parts.push(['他第一次晃到这一档 · 接下来两轮是机会', 'good']);
  if (!parts.length) return null;
  const box = document.createElement('div');
  parts.forEach(([text, cls]) => {
    const el = document.createElement('div');
    el.className = 'window' + (cls ? ' ' + cls : '');
    el.textContent = text;
    box.appendChild(el);
  });
  return box;
}

/** 画那根 K 线。
 *
 * **不赌布局时序。** 原来这里直接读 `clientWidth` 就画，注释写着"读它本身
 * 就会强制布局，不必等下一帧"——那句话在这条路径上不成立：`.review` 刚被
 * 塞进 body、还带着一个 slidein 动画，实测量到的是 **16px**（`width:100%`
 * 落在一个还没有宽度的容器上，再加左右各 8px 内边距）。画布按 16px 画完，
 * CSS 再把它拉到全宽，K 线就糊成一片。
 *
 * 而且它是**间歇性**的——同一段代码有时对有时不对，这种视觉 bug 最坏：
 * 平时看不出来，路演当天糊给评审看。
 *
 * 所以量到不合理的宽度就等下一帧重画，而不是相信第一次的结果。
 */
function paintChart(view) {
  const chart = view.querySelector('#chart');
  if (!chart) return;
  const w = chart.clientWidth;
  const h = chart.clientHeight;
  // 一根蜡烛最窄 3px、最多 12 轮，再加边距——比这还窄只可能是没量准
  if (w < 100 || h < 40) {
    requestAnimationFrame(() => paintChart(view));
    return;
  }
  paintKline(fitCanvas(chart, w, h), { x: 10, y: 12, w: w - 20, h: h - 26 }, {
    turns: game.turns,
    threshold: game.threshold,
    slots: Math.max(game.turns.length, 6),
    palette: palette(),
    axis: true,
    // 第 1 轮判分之前的信任度，就是开局那个数。不另外记一份：
    // 记两份迟早走散，而这一份本来就在逐轮数据里
    start: game.turns.length ? game.turns[0].before : null,
  });
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

/** 分享卡 = 复盘正文里"结算卡 + 三栏统计 + K 线 + 百分比"这一整块，重画一遍。
 *
 * 上一版分享卡是他说过的一句话（聊天气泡截图），现在这一块换成了复盘正文
 * 本身的内容——两者不再是两套东西：卡上有什么，正文往上翻就看得到。
 * K 线直接复用 `paintKline`，画法与屏幕上那张一模一样，不用另起一套逻辑。
 */
function makeCard(view) {
  const W = 640;
  const pad = 40;
  const contentW = W - pad * 2;
  const c = palette();
  const kind = game.ending ? game.ending.kind : 'transferred';
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
  const FOOT_TOP = PCT_TOP + PCT_H + (pct != null ? 16 : -8);
  const H = FOOT_TOP + 56;

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

  // 钩子放在右下角，正好接住下一步的"接话"入口
  ctx.fillStyle = c.sub;
  ctx.font = `400 14px ${c.sans}`;
  if (game.contestId) ctx.fillText(`参赛编号 ${game.contestId}`, pad, FOOT_TOP + 22);
  ctx.textAlign = 'right';
  ctx.fillStyle = c.gray;
  ctx.font = `500 16px ${c.sans}`;
  ctx.fillText('你的客户这么说，你怎么接？', W - pad, FOOT_TOP + 18);
  ctx.textAlign = 'left';

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

// 随机派发页至少停留一个短节拍：让用户知道这是一位由系统分配的真实客户，
// 又不把等待演成抽卡。低动态偏好下不增加人为等待。
showScreen('assignment');
const assignmentStartedAt = Date.now();
let startError = null;

async function loadGame() {
  const resp = await fetch('api/game/start', { method: 'POST' });
  if (!resp.ok) throw new Error(`start failed: ${resp.status}`);
  const data = await resp.json();
  game.token = data.token;
  game.opening = data.opening;
  game.trust = data.trust;
  game.mood = data.mood;
  game.threshold = data.win_threshold;
  game.maxRounds = data.remaining;
  game.remaining = data.remaining;
  $('turnCurrent').textContent = '1';
  $('turnTotal').textContent = String(game.maxRounds);
  $('roundFill').style.width = `${100 / game.maxRounds}%`;
  game.contestId = data.contest_id || '';
  SCENE = data.scenario || null;
  if (!SCENE) throw new Error('start response missing scenario');
  paintDesk();
  paintOpening();
}

const ready = (async () => {
  try {
    await loadGame();
    const elapsed = Date.now() - assignmentStartedAt;
    if (!REDUCED && elapsed < 850) await sleep(850 - elapsed);
    showScreen('opening');
    return true;
  } catch (error) {
    startError = error;
    $('assignmentTitle').textContent = '暂时无法接入客户。';
    $('assignmentCopy').textContent = '请检查网络或服务状态后重新连接，本局尚未开始。';
    $('assignmentProgress').hidden = true;
    $('retryStart').hidden = false;
    return false;
  }
})();

/** 事件开场只讲账户这一侧能确认的事实，不提前泄露诈骗类型。 */
function paintOpening() {
  if (!SCENE) return;
  $('openingTitle').textContent = SCENE.incident.title;
  $('openingLead').textContent = SCENE.incident.lead;
  $('openingMoney').textContent = wholeMoney(TOTAL());
  $('openingHint').textContent = SCENE.incident.hint;
  $('openingAlert').textContent = '资金异动 · 等待处理';
  paintTodayCount();
}

/** 抬头那行「今日第 N 位客户」。
 *
 * **原先写死成「今日 1 / 3」。** 那个分母是假的：客户由系统随机派发，
 * 一局一位，没有"今天一共三位"这回事，四个场景上线之后更对不上。
 * 界面上任何一个数都该有出处——这里的出处是本机训练记录里今天的局数
 * （localStorage，与复盘那一节同源）。读不到就退回"今日第 1 位客户"，
 * 不因为一个装饰性的数字让开场屏崩掉。
 */
function paintTodayCount() {
  const el = $('todayCount');
  if (!el) return;
  let n = 1;
  try {
    const today = new Date().toDateString();
    n = loadHistory().filter((e) => new Date(e.ts).toDateString() === today).length + 1;
  } catch {
    n = 1;
  }
  el.textContent = `今日第 ${n} 位客户`;
}

/** 客户档案里的一行。 */
function factRow(f) {
  const row = document.createElement('div');
  row.className = 'fact' + (f.warn ? ' warn' : '');
  const dt = document.createElement('dt');
  dt.textContent = f.label;
  const dd = document.createElement('dd');
  const v = document.createElement('span');
  v.className = 'num';
  v.textContent = f.value;
  dd.appendChild(v);
  if (f.note) {
    const em = document.createElement('em');
    em.textContent = f.note;
    dd.appendChild(em);
  }
  row.append(dt, dd);
  return row;
}

/** 把场景素材铺到工作台上。**这一屏此前是写死的老陈档案。**
 *
 * 铺的东西必须和判分那边是同一个场景——判分按场景换了效力矩阵，
 * 界面这边要是还印着三十万和王老师，玩家看到的就是两个骗局拼在一起。
 */
function paintDesk() {
  if (!SCENE) return;
  const c = SCENE.client;
  $('cName').textContent = c.name;
  $('cSub').textContent = c.sub;
  $('cTag').textContent = c.tag;
  document.querySelector('.ccard-top .avatar').textContent = c.name.slice(0, 1);
  $('peer').textContent = SCENE.peer;
  $('ctaLabel').textContent = `给${SCENE.peer}发消息`;
  // 交底文案来自我们自己的场景表，不是用户输入
  $('deskNote').innerHTML = SCENE.note.join('<br>');
  $('say').setAttribute('aria-label', `跟${SCENE.peer}说`);
  $('say').setAttribute('placeholder', `输入你想对${SCENE.pronoun}说的话`);

  // **warn 的那几行是牌，默认摊开；其余是背景，收进「展开」。**
  // 这是 app/scenario.py 里写着的设计意图，之前被改成"六行一次全铺开"了。
  // 全铺开有两处代价：一是那组矛盾（保守型 × 持仓清空 × 47 笔）和
  // 「开户 19 年」变成一样重，玩家一条都记不住；二是 375px 上把档案顶到
  // 折叠线以下，最后一行正好被切在屏幕边缘——**看着像坏了，不像能滚**。
  const box = $('cFacts');
  const warn = c.facts.filter((f) => f.warn);
  const rest = c.facts.filter((f) => !f.warn);
  box.innerHTML = '';
  warn.forEach((f) => box.appendChild(factRow(f)));

  const more = $('factsMore');
  const label = $('factsMoreLabel');
  if (!rest.length) {
    more.hidden = true;
    return;
  }

  more.hidden = false;
  let open = false;
  let extra = [];
  const paint = () => {
    label.textContent = open ? '收起' : `其余 ${rest.length} 项账户信息`;
    more.setAttribute('aria-expanded', String(open));
    more.classList.toggle('open', open);
  };
  more.onclick = () => {
    open = !open;
    if (open) {
      extra = rest.map((f) => box.appendChild(factRow(f)));
    } else {
      extra.forEach((el) => el.remove());
      extra = [];
    }
    paint();
  };
  paint();
}

function showScreen(id) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.toggle('on', s.id === id));
}

// 这台设备见过那一屏没有。**只认"见过"，不认"同意"**——它是课程表，
// 不是条款，没有需要玩家承诺的东西。存不进去（隐私模式）就每次都显示，
// 这是安全的失败方向：多看一屏，好过第一局空手上阵。
const PRIMER_KEY = 'aap.primer.seen';

function primerSeen() {
  try {
    return localStorage.getItem(PRIMER_KEY) === '1';
  } catch {
    return false;
  }
}

function markPrimerSeen() {
  try {
    localStorage.setItem(PRIMER_KEY, '1');
  } catch {
    // 存不了就下次再看一遍，不影响任何别的东西
  }
}

/** 开打前那一屏。**只列名字与一句话**，见 KEYS 顶部那段关于 brief / tip 的注释。 */
function paintPrimer() {
  const list = $('primerList');
  if (!list || list.childElementCount) return;
  Object.keys(KEYS).forEach((k) => {
    const li = document.createElement('li');
    const name = document.createElement('b');
    name.textContent = KEYS[k].name;
    const desc = document.createElement('span');
    desc.textContent = KEYS[k].brief;
    li.append(name, desc);
    list.appendChild(li);
  });
}

async function enterGame() {
  if (game.entered) {
    showScreen('chat');
    $('say').focus();
    return;
  }

  // 第一次开打先过一遍课程表。**放在开局请求之后、进聊天之前**：
  // 开局请求在玩家读工作台那一屏时就发出去了，读这一屏的时间同样在给它买单，
  // 「首屏 ≤3 秒」不受影响。
  if (!primerSeen()) {
    paintPrimer();
    showScreen('primer');
    return;
  }

  const started = await ready;
  if (!started || startError) {
    showScreen('assignment');
    return;
  }

  game.entered = true;
  showScreen('chat');
  $('remaining').textContent = String(game.remaining);
  $('turnCurrent').textContent = '1';
  $('roundFill').style.width = `${100 / game.maxRounds}%`;
  paintMood(game.mood);
  divider('下午 2:47');
  say('me', PING());
  say('them', game.opening);
  $('say').focus();
}

function startNewClient() {
  location.reload();
}

// 它现在是个真 <button>，回车与空格由浏览器自己管，不用再补 keydown
$('openChen').addEventListener('click', enterGame);

// 「跳过」与「知道了」走同一条路：都记下已看过，然后进聊天。
// 跳过不该被惩罚——愿意空手上阵的人本来就是这个作品最想要的那批玩家。
['primerGo', 'primerSkip'].forEach((id) => {
  const el = $(id);
  if (el) el.addEventListener('click', () => { markPrimerSeen(); enterGame(); });
});
$('backHome')?.addEventListener('click', () => showScreen('home'));
$('openProfile').addEventListener('click', () => showScreen('home'));
$('backOpening').addEventListener('click', () => showScreen('opening'));
$('retryStart').addEventListener('click', startNewClient);

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
