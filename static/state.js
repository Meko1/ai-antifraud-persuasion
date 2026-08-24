/* 会话状态机：这一局是什么、走到哪儿了、算出来是什么结果。
 *
 * **这一层不碰 DOM。** 复盘里全部的"这一局你打得怎么样"都从这儿算，
 * 而那正是它能被单测覆盖的原因（tests/frontend/review.test.mjs）。
 *
 * SCENE 是 let，开局那一下才被赋值。ES module 的导入绑定是只读的，
 * 别的模块因此不能直接写它——改用 setScene()。读仍然直接读 SCENE：
 * 导入绑定是活的，赋值之后各处看到的都是新值。 */

import { BREACHES } from './keys.js';

// ── 状态 ────────────────────────────────────────────────────

export const game = {
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
  // 这一局是被什么触发的、属于哪个实验组、是不是演示态（服务端下发）
  origin: null,
  // 可选客户清单。**只有演示态才有内容**——真实接入时场景由用户自己
  // 那笔异动决定，界面上不该出现"换一位客户"
  catalog: [],
  // 用户主动结束了这次对话。它让复盘走 `unfinished` 那一档：
  // 判分照常，但不编造一个资金结局（见 reviewKind / resultAmount）
  exited: false,
};

// ── 剧本素材 ────────────────────────────────────────────────────
//
// **金额、收款方、客户档案、揭晓清单原先全写死在这里和 index.html 里。**
// 加第二个场景时那些地方没有一处会提醒你漏改了——于是它们搬去了服务端
// （app/scenario.py 的 `payload`），开局随 /api/game/start 下发。
//
// 这不只是整洁：判分那边按场景换了效力矩阵，界面这边要是还印着三十万和
// 王老师，玩家看到的就是两个不同的骗局拼在一起。
export let SCENE = null;

export const TOTAL = () => (SCENE ? SCENE.money.total : 300000);
// 拦下那一档里仍有一小笔钱被转走。这是骗局的标准剧本——先小额取信——
// 代价是玩家表现不错、结局仍有人损失。取真实。
export const TEST_TRANSFER = () => (SCENE ? SCENE.money.test_transfer : 20000);
export const PAYEE = () => (SCENE ? SCENE.money.payee : '');

// 你先发的那一条，聊天窗口的第一条——**他的开场白是在回它**，
// 少了它他就是在回应空气。
// 内容只能有一条信息：账户动了。骗局的一切开局你一概不知道（CONTEXT.md「对局」）。
export const PING = () => (SCENE ? SCENE.ping : '');
export const peerInitial = () => (SCENE ? SCENE.initial : '陈');
// 状态条上那个「他/她」。周淑琴那一局写「他现在」，玩家一眼看出界面是照别人做的
export const peerPronoun = () => (SCENE ? SCENE.pronoun : '他');

/** 施压那一轮的旁白。**由服务端按场景下发**（`Scenario.pressure_note`）。
 *
 *  原先三处都写死成「王老师又在群里催了一遍」——而顾之然没有王老师，
 *  月娥姐的催单来自群主，周淑琴那边是"办案的"在电话里催。
 *  玩家在聊天窗口里看到一个本局根本不存在的人名，比台词平庸严重得多：
 *  台词平庸还能用"骗子本来就说车轱辘话"糊过去，人名穿帮糊不过去。
 *
 *  与 `SAFE_FALLBACK` 那个 bug 是同一类：**剧本常量留在了场景之外**。 */
export const pressureNote = () => (SCENE && SCENE.pressure_note) || '对方又在催了一遍';

export const money = (n) =>
  '¥' + n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// 复盘头部那个大数不带角分：转账凭证上要两位小数（那是单据），
// 结算卡上不要（那是结果）。¥300,000.00 里的 .00 只会把字号占掉。
export const wholeMoney = (n) => '¥' + n.toLocaleString('zh-CN');

/** 换掉当前场景。**只此一个写入口。**
 *
 *  ES module 里 `import { SCENE }` 拿到的是只读绑定，
 *  `SCENE = x` 会当场抛错。写只能发生在这个模块里。 */
export function setScene(next) {
  SCENE = next;
}

/** 这一局踩了几次红线。判分口径在服务端，前端只认下发的 hits。 */
export function breachTurns() {
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
export function scoredTurns() {
  return game.turns.filter((t) => !t.degraded);
}

/** 这一局替客户守住了多少钱。
 *
 * 只有劝住与拦下真的留住了钱；拖住、转账、被拉黑都是 0。
 * **0 是这张卡上最该被看见的那个数** —— "钱一分没动，也一分没保住"
 * 那句话说了半天，不如一个 ¥0 来得重。
 */
export function savedAmount(kind) {
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
export function resultAmount(kind) {
  // 主动结束：**这一局没有走到任何一个结局，所以它没有资金状态。**
  //
  // 在此之前没有这个分支，`openReview` 拿不到 ending 就落到 `'transferred'`——
  // 于是一个第 3 轮自己点"结束"的用户，会看到"¥300,000 已全部转出"。
  // 那是一句凭空捏造的结果，而且是最伤人的那种：他什么都没做错，
  // 系统告诉他钱没了。
  if (kind === 'unfinished') {
    return { value: '未产生结果', label: '这次对话由你主动结束，没有走到结局' };
  }
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
export const RECEIPT = {
  persuaded: 'void', intercepted: 'sent', stalled: 'hold',
  transferred: 'sent', blacklisted: null,
  // 主动结束不出凭证：那张卡说的是"这笔钱最后怎么了"，而这一局没走到那一步
  unfinished: null,
};

// 复盘页的四色语义（设计稿的 data-tone）。见 openReview 里那段注。
export const TONES = {
  persuaded: 'green',
  intercepted: 'gold',
  stalled: 'plain',
  transferred: 'rust',
  blacklisted: 'rust',
  // 中性灰。**不给红**：主动退出不是失败，它是这个产品明确允许的动作
  // （定位文档：不做成必须通过才能交易的障碍）。用红色标它，
  // 等于一边给退出按钮、一边惩罚按下去的人
  unfinished: 'plain',
};

// 结算卡上那个大数的**出处**。
//
// 上面那个金额来自模拟信任度跨没跨过一条线（app/scoring.py 的结局阶梯），
// 不来自任何交易系统。代码里早就在注释中承认了这一点
// （`resultAmount` 顶部那段"「确认保住」这四个字 8-22 拿掉了"），
// **但界面上从来没说过**——玩家看到的仍然是一个三十万的大数配一句
// "他最后没按下确认"，读起来就是"这个产品挽回了三十万"。
//
// 分两种情况说，因为它们的性质确实不同：
// · 走到结局的 —— 那是**虚构客户的反应**，是模拟出来的
// · 主动结束的 —— 压根没有结果，别装作有
export const RESULT_BASIS = {
  unfinished:
    '这一局没有走到结局，因此没有资金状态。真实交易是否取消，只能由交易系统回传。',
  // 走 textContent，所以这里不写 markdown 记号——写了会原样印在页面上
  default:
    '上面这个状态是虚构客户在本次模拟中的反应，由对话判分推算得出，'
    + '不是真实交易结果。真实资金状态以交易系统回传为准。',
};

/** 这一局按哪个结局来复盘。
 *
 * **只此一份。** 原先三处各写一遍 `game.ending ? game.ending.kind : 'transferred'`，
 * 那个兜底值在"打满十二轮但事件丢了"的场景下是对的，在"用户主动结束"
 * 的场景下是一句谎话。判据集中到这里，三处就不会各自漂移。
 */
export function reviewKind() {
  if (game.ending) return game.ending.kind;
  return game.exited ? 'unfinished' : 'transferred';
}

export function endingMeta(kind) {
  if (kind === 'unfinished') {
    const n = game.turns.length;
    return {
      tier: '未完成',
      title: n ? `你在第 ${n} 轮结束了这次对话` : '你没有开始这次对话',
      savedCopy: '下面是你已经说过的那几轮，判分照常。',
      receipt: null,
      amount: 0,
    };
  }
  const copy = (SCENE && SCENE.endings[kind]) || {};
  return {
    tier: copy.tier || '转账',
    title: copy.title || '他还是转走了',
    savedCopy: copy.saved || '',
    receipt: RECEIPT[kind] !== undefined ? RECEIPT[kind] : 'sent',
    amount: kind === 'intercepted' ? TEST_TRANSFER() : TOTAL(),
  };
}

export const ERRORS = {
  invalid_state: '这一局放得太久了，得重开一局',
  upstream_unavailable: '消息没发出去，再说一次试试',
  rate_limited: '这会儿人有点多，等两秒再说',
  internal: '出了点岔子，再说一次试试',
  network: '网络断了，这句没发出去',
  // 服务端挡下了一张已经打完的令牌。正常玩不会碰到——碰到多半是这一轮的
  // 结果没能回到手上（网络在中途断了），而服务端那边已经算完了。
  replayed: '这一轮已经算过了，页面和他那边对不上，得重开一局',
};

// ── 断线续局 ──────────────────────────────────────────────────────
//
// **刷新一次整局就没了。** 在旧定位下这只是个瑕疵；转向之后它是硬伤：
// 用户正要转账时被弹出这一屏，切出去看一眼真实的转账页面再回来，
// 是最自然不过的动作——而手机在内存吃紧时会把后台标签整个重载。
// 回来发现要从头再打十二轮，他不会从头再打，他会去把那笔钱转了。
// **完成率是转向之后定下的护栏指标之一，这一条直接吃掉它。**
//
// 用 sessionStorage 不用 localStorage：这是"同一次会话里接着打"，
// 不是"三天后接着打"。标签页关掉就该干净——一次干预只属于这一刻，
// 隔天弹出一句"要不要接着上次那局"是骚扰，不是服务。
//
// **服务端仍然什么都不存**（ADR-0003 没被动）：判分状态一直在签名令牌里，
// 这里存的只是前端这一侧的展示状态。令牌自带 2 小时有效期，TTL 与它对齐——
// 拿一个服务端必然拒绝的令牌去续局，换来的只是一句看不懂的错误提示。
export const RESUME_KEY = 'aap.game.v1';
export const RESUME_TTL_MS = 2 * 60 * 60 * 1000;

// 「换一位客户」挑中的那一位，交给下一次加载。用 sessionStorage 不用 URL 参数：
// 它是一次性的意图，不该留在地址栏里被分享或被刷新重放。
export const PICK_KEY = 'aap.pick.sid';

export function saveGame() {
  if (!game.token || !SCENE) return;
  try {
    sessionStorage.setItem(RESUME_KEY, JSON.stringify({
      savedAt: Date.now(),
      scene: SCENE,
      notice: game.notice || '',
      game: {
        token: game.token, contestId: game.contestId, opening: game.opening,
        trust: game.trust, mood: game.mood, threshold: game.threshold,
        maxRounds: game.maxRounds, remaining: game.remaining,
        turns: game.turns, ending: game.ending, quote: game.quote,
        // 续局要还原"他已经主动结束过了"，否则刷新一次退出就被撤销了，
        // 而复盘会重新落回那个凭空捏造的 transferred
        exited: game.exited, origin: game.origin, catalog: game.catalog,
      },
    }));
  } catch {
    // 隐私模式 / 配额满：续局能力没了，这一局照常打得完。
    // **不提示**——它是兜底，不是功能，玩家没有为此做任何决定
  }
}

export function loadSaved() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(RESUME_KEY) || 'null');
    if (!saved || !saved.game || !saved.game.token || !saved.scene) return null;
    if (Date.now() - saved.savedAt > RESUME_TTL_MS) {
      clearSaved();
      return null;
    }
    return saved;
  } catch {
    return null;
  }
}

export function clearSaved() {
  try { sessionStorage.removeItem(RESUME_KEY); } catch { /* 同上 */ }
}

export function startNewClient(sid) {
  // **必须先清。** 它走的是 location.reload()，而启动那一步现在会优先续局——
  // 不清的话这个按钮会把刚打完的那一局原样再放一遍
  clearSaved();
  // 指定客户时把它交给下一次加载。用 sessionStorage 而不是 URL 参数：
  // 这是一次性的意图，不该留在地址栏里被分享出去或被刷新重放
  try {
    if (typeof sid === 'string' && sid) sessionStorage.setItem(PICK_KEY, sid);
    else sessionStorage.removeItem(PICK_KEY);
  } catch { /* 隐私模式：那就随机来一位，不影响任何别的东西 */ }
  location.reload();
}
