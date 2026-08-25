import { $, REDUCED, showScreen, sleep, thread } from './dom.js';
import { KEYS } from './keys.js';
import { startGame } from './api.js';
import { openSheet } from './sheet.js';
import { loadHistory } from './history.js';
import { divider, paintMood, resumeGame, say } from './chat.js';
import {
  PICK_KEY, PING, SCENE, TOTAL, clearSaved, game, loadSaved, saveGame, setScene,
  startNewClient, wholeMoney,
} from './state.js';

// ── 开局 ────────────────────────────────────────────────────

let savedGame = null;
let assignmentStartedAt = 0;
let startError = null;

/* ── 转账确认屏 ──────────────────────────────────────────────
 *
 * 开局请求在这一屏后面照常跑，玩家读转账单的时间同样在给「首屏 ≤3 秒」买单
 * ——和工作台那一屏是同一笔账。
 *
 * `bootOutcome` 与 `transferAcked` 两个状态凑在一起决定"什么时候切到工作台"：
 * 请求可能比玩家先到（那就等他签完字），玩家也可能比请求先到
 * （那就先给他接入动画）。少任何一个，都会出现"他还在看转账单，
 * 工作台自己蹦出来了"。 */
const TRANSFER_KEY = 'aap.transfer.seen';
let bootOutcome = 'pending';   // pending | ready | error
let transferAcked = true;

function transferSeen() {
  try {
    return sessionStorage.getItem(TRANSFER_KEY) === '1';
  } catch {
    return false;  // 隐私模式：那就再演一遍，不致命
  }
}

/** 开局那一路要等的东西。`boot()` 之前它是"还没开始"，不是 undefined——
 *  `enterGame` 无条件 await 它，给个空壳省得那里再判一次。 */
let ready = Promise.resolve(false);

/** 点火。**由入口 app.js 调用，不在这个模块加载时自己跑。**
 *
 *  理由是模块求值顺序：opening 与 chat / review 之间有环（续局要重画聊天，
 *  聊天打完要开复盘），而环里谁先求值取决于 import 的书写顺序。
 *  在模块体里直接开跑，`resumeGame()` 就可能在 review.js 的 `const` 还没
 *  初始化时去读它——**那是一个只在"有存档 + 刷新"时才出现的 TDZ 崩溃**，
 *  平时开发根本碰不到。挪到入口之后，所有模块体都求值完了才点火。
 *
 *  开局请求的时机没有变：入口的模块体仍然是首屏加载时同步跑完的。 */
export function boot() {
  // 随机派发页至少停留一个短节拍：让用户知道这是一位由系统分配的真实客户，
  // 又不把等待演成抽卡。低动态偏好下不增加人为等待。
  // 有存档就直接进对话，不闪那一下"正在接入高风险客户"——
  // 续局的人不是在开新局，给他看接入动画是在撒谎
  savedGame = loadSaved();
  // 转账确认屏**只在这次会话的第一局出现**。「换一位客户」走的是
  // location.reload()，每换一位就重演一遍签字，它就从"一次经历"退化成
  // "一段过场动画"——而过场动画是会被跳过的东西。
  transferAcked = Boolean(savedGame) || transferSeen();
  showScreen(savedGame ? 'chat' : (transferAcked ? 'assignment' : 'transfer'));
  assignmentStartedAt = Date.now();
  ready = startSession();
  return ready;
}

/** 按下「确认转出」：不切屏，只把这一屏换成拦截那一面。
 *  切屏留给下一步——**这笔转账被拦下来这件事，要发生在同一屏上**，
 *  换个屏幕就变成了两件不相干的事。 */
export function confirmTransfer() {
  $('transferForm').hidden = true;
  $('transferFoot').hidden = true;
  $('transferHandoff').hidden = false;
  $('handoffFoot').hidden = false;
  // 焦点跟着走，否则键盘用户按完确认之后焦点掉回 body
  $('handoffGo').focus({ preventScroll: true });
}

/** 按下「坐到对面」：真正离开转账屏。
 *  开局请求已经回来就直接进工作台，没回来就去接入动画那一屏等着。 */
export function ackTransfer() {
  try {
    sessionStorage.setItem(TRANSFER_KEY, '1');
  } catch { /* 隐私模式：下次刷新再演一遍，不影响任何别的东西 */ }
  transferAcked = true;
  showScreen(bootOutcome === 'ready' ? 'opening' : 'assignment');
}

export async function loadGame() {
  // 挑中的那一位客户（「换一位客户」按钮存下的）。**读完就清**：
  // 它是一次性的意图，留着的话下一次刷新会莫名其妙又是同一个人。
  let picked = '';
  try {
    picked = sessionStorage.getItem(PICK_KEY) || '';
    sessionStorage.removeItem(PICK_KEY);
  } catch { /* 隐私模式：随机来一位 */ }

  const data = await startGame(picked);
  game.origin = data.origin || null;
  game.catalog = data.catalog || [];
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
  // 开口之前那句告知（ADR-0006）。**文案在服务端**：留存开着和关着说的不是
  // 同一句话，而这一页写死一份的话，早晚会出现"页面说不留存、服务端在留存"。
  // 下发不到就保留 index.html 里那句静态兜底，不清空——**这一行绝不能是空的**。
  if (data.notice) {
    document.querySelectorAll('.strangertip').forEach((el) => {
      el.textContent = data.notice;
    });
  }
  setScene(data.scenario || null);
  if (!SCENE) throw new Error('start response missing scenario');
  game.notice = data.notice || '';
  paintDesk();
  paintOpening();
  paintClientPicker();
  saveGame();
}

/** 「换一位客户」只在演示态出现。
 *
 *  判据是服务端下发的 `catalog` 有没有内容——真实接入时它是空数组
 *  （app/main.py 的 `game_start`），前端不自己判断这件事：
 *  "什么时候允许挑客户"是业务规则，规则只该有一处。
 */
export function paintClientPicker() {
  const btn = $('pickClient');
  if (!btn) return;
  btn.hidden = !(game.catalog && game.catalog.length > 1);
}

async function startSession() {
  if (savedGame) {
    try {
      resumeGame(savedGame);
      return true;
    } catch (error) {
      // 存的东西和现在这版代码对不上（改过字段、换过场景 id）就当没存过。
      // **要把已经画出去的那半截清掉**，否则新的一局会接在旧对话后面
      clearSaved();
      thread.replaceChildren();
    }
  }
  try {
    await loadGame();
    const elapsed = Date.now() - assignmentStartedAt;
    if (!REDUCED && elapsed < 850) await sleep(850 - elapsed);
    bootOutcome = 'ready';
    // 玩家还在转账确认屏上就别切走——他签完字自己会过来（ackTransfer）
    if (transferAcked) showScreen('opening');
    return true;
  } catch (error) {
    startError = error;
    bootOutcome = 'error';
    $('assignmentTitle').textContent = '暂时无法接入客户。';
    $('assignmentCopy').textContent = '请检查网络或服务状态后重新连接，本局尚未开始。';
    $('assignmentProgress').hidden = true;
    $('retryStart').hidden = false;
    return false;
  }
}

/** 事件开场只讲账户这一侧能确认的事实，不提前泄露诈骗类型。 */
export function paintOpening() {
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
 * 界面上任何一个数都该有出处——这里的出处是本机对局记录里今天的局数
 * （localStorage，与复盘那一节同源）。读不到就退回"今日第 1 位客户"，
 * 不因为一个装饰性的数字让开场屏崩掉。
 */
export function paintTodayCount() {
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
export function factRow(f) {
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
export function paintDesk() {
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

// 这台设备见过那一屏没有。**只认"见过"，不认"同意"**——它是课程表，
// 不是条款，没有需要玩家承诺的东西。存不进去（隐私模式）就每次都显示，
// 这是安全的失败方向：多看一屏，好过第一局空手上阵。
const PRIMER_KEY = 'aap.primer.seen';

export function primerSeen() {
  try {
    return localStorage.getItem(PRIMER_KEY) === '1';
  } catch {
    return false;
  }
}

export function markPrimerSeen() {
  try {
    localStorage.setItem(PRIMER_KEY, '1');
  } catch {
    // 存不了就下次再看一遍，不影响任何别的东西
  }
}

/** 开打前那一屏。**只列名字与一句话**，见 KEYS 顶部那段关于 brief / tip 的注释。 */
export function paintPrimer() {
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

export async function enterGame() {
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

/** 换一位客户。**只在演示态出现**（见 index.html 里那段注）。 */
export function openClientSheet() {
  const list = (game.catalog || []).filter((c) => !SCENE || c.id !== SCENE.id);
  if (!list.length) return;
  openSheet({
    title: '换一位客户',
    note: '换人会重开一局。当前这一局不会保留。',
    items: list.map((c) => ({
      label: `${c.client} · ${c.name}`,
      note: c.headline,
      onPick: () => startNewClient(c.id),
    })).concat([{
      label: '随机一位',
      note: '和真实接入时一样，由系统按异动类型分配。',
      onPick: () => startNewClient(''),
    }]),
  });
}
