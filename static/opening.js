import { $, REDUCED, showScreen, sleep, thread } from './dom.js';
import { KEYS } from './keys.js';
import { startGame } from './api.js';
import { openSheet } from './sheet.js';
import { loadHistory } from './history.js';
import { divider, paintMood, resumeGame, say } from './chat.js';
import {
  PICK_KEY, PING, SCENE, TOTAL, clearSaved, game, loadSaved, peerPronoun,
  saveGame, setScene, startNewClient, wholeMoney, withTa,
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

/** 这一次会话里，他有没有自己按过那个「确认转出」。
 *
 *  复盘那块「回到你自己那一笔」拿它决定开场怎么说：**没演过就不能说
 *  "三分钟前你也按了确认"**——那一屏一个会话只演一次（换客户、再开一局
 *  都不会重演），隐私模式下也可能从来没写进去过。
 *  这个仓库不许对玩家断言一件没发生的事，哪怕它多半发生了。 */
export function transferSeen() {
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

/** 拦截面就位之后，「坐到对面」被锁住的时长。
 *
 *  **这不是动效参数，是一条防线。** 两个按钮共用页脚同一个像素矩形
 *  （实测都是 `[19,716,337,52]`），而换面是 0ms 硬切——间隔 130ms 在同一坐标
 *  点两下，第一下命中「确认转出」，第二下命中已经就位的「坐到对面」，
 *  **拦截面一帧都没渲染就落到工作台了**；而 `ackTransfer()` 已经写下
 *  `aap.transfer.seen`，刷新也回不来（键在 sessionStorage，清 localStorage 无效）。
 *  这个作品唯一的核心时刻，可以被一次手滑永久删掉。
 *
 *  450ms 顺便还是这一屏缺的那一拍呼吸：转向需要一拍，它原先一拍都没有。
 *  低动态偏好下不制造人为等待，但**锁仍然要上**——只是立刻解开，
 *  它挡的是同一串连击，不是慢手。 */
const HANDOFF_ARM_MS = 450;

/** 拦截那一下的提示音与震动。
 *
 *  **放在按下「确认转出」那一刻，不放在开屏**——这不是取舍，是浏览器的规矩：
 *  `AudioContext` 与 `navigator.vibrate()` 都要求页面先发生过一次真实交互
 *  （Chrome 的 autoplay / user-activation 策略），"一进页面就响"那一版在
 *  桌面 Chrome 上根本不会响，只会静默失败。而按下确认恰好是这一屏戏剧性
 *  最强的一拍：钱正要出去，系统把它摁住了——声音落在这里比落在开屏更对。
 *  开屏那一下的告警交给纯视觉的边缘晕染（`.transfer-alarm`），它不受这条限制。
 *
 *  低动态偏好下两样都跳过。`prefers-reduced-motion` 严格说管的是动效不是声音，
 *  但设了这条的人要的是"别惊动我"，而一声毫无预告的提示音正是惊动。
 *
 *  全程 try/catch、不 await：不支持 Web Audio 的浏览器、被策略拦下的调用、
 *  iOS 上根本不存在的 `vibrate`——任何一个都不许挡住拦截面翻开。 */
function alarmFeedback() {
  if (REDUCED) return;
  try { navigator.vibrate?.([28, 60, 28]); } catch { /* 不支持震动就算了 */ }
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const at = ctx.currentTime;
    // 两声下行（C6 → G5）。**下行读起来是"被摁住了"，上行是"办好了"**——
    // 这一下要说的是前者。峰值 .05：它是一句提示音，不是音效。
    [[1046.5, 0], [784, .12]].forEach(([hz, off]) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = hz;
      gain.gain.setValueAtTime(.0001, at + off);
      gain.gain.exponentialRampToValueAtTime(.05, at + off + .012);
      gain.gain.exponentialRampToValueAtTime(.0001, at + off + .11);
      osc.connect(gain).connect(ctx.destination);
      osc.start(at + off);
      osc.stop(at + off + .12);
    });
    // 放完就关：别把一个 AudioContext 挂到会话结束，浏览器对同时存在的
    // 上下文数量是有上限的。
    setTimeout(() => { ctx.close().catch(() => {}); }, 600);
  } catch { /* 拿不到音频就静默播出，这一屏其余部分一个字都不受影响 */ }
}

/** 按下「确认转出」：不切屏，只把这一屏换成拦截那一面。
 *  切屏留给下一步——**这笔转账被拦下来这件事，要发生在同一屏上**，
 *  换个屏幕就变成了两件不相干的事。 */
export function confirmTransfer() {
  alarmFeedback();
  $('transferForm').hidden = true;
  $('transferFoot').hidden = true;
  $('transferHandoff').hidden = false;
  $('handoffFoot').hidden = false;

  // 身份那一行的 `{ta}`。**在这里换而不是在 `paintTransfer()` 里**：
  // 那个函数按设计"玩家已经签过字就不动"（改写发生在他眼皮底下会闪），
  // 而这一行属于翻开的这一面，此刻才第一次被看见。
  //
  // 开局请求慢到还没回来时 `peerPronoun()` 退回「他」——与改动前一模一样，
  // 不会更差；正常情况下它早回来了（同一屏的金额就是它改写的）。
  const role = $('handoffRole');
  if (role) {
    role.innerHTML = withTa('你是{ta}的投资顾问：看得见{ta}的账户，看不见{ta}的生活。'
      + '你可以问，<b>但不能替{ta}做决定，也不能向{ta}推荐任何产品</b>——那是执业红线。');
  }

  const go = $('handoffGo');
  go.disabled = true;
  setTimeout(() => { go.disabled = false; }, REDUCED ? 0 : HANDOFF_ARM_MS);

  // 焦点送**标题**，不送按钮。送按钮的话，屏幕阅读器用户按完「确认转出」
  // 听到的唯一一句是「坐到对面，按钮」——他有充分理由认为转账成功了，
  // 而这一屏想说的每一个字都被跳过去了。理由与取舍见 index.html 那段注。
  $('handoffTitle').focus({ preventScroll: true });
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
  paintTransfer();
  paintClientPicker();
  // **首屏也要在这儿重刷一次。** 它可能已经画出来、已经在玩家眼前了——
  // `enterGame()` 显示那一屏时不等这个请求（那是有意的）。代词没变的话
  // `paintPrimer()` 自己那道闸会当场返回，不花任何代价；变了才重画。
  // 少这一行，上面那道闸就没有人去触发，慢网下七条永远停在默认「他」。
  paintPrimer();
}

/** 把转账确认屏那两处可变内容改写成**本局这位客户**的数字。
 *
 *  为什么必须做这件事：拦截面承诺「有人正要做同样的事」，而这一屏原先照老陈
 *  写死（30 万 / 今早清仓）。客户是 `scenario_for_trigger` 按异动分配的——
 *  抽到刘卫东（35 万理财赎回、近三月转 6 次、**没有清仓**）时，金额、品种、
 *  信号三项全不一致，那句承诺被自己的第一屏当场推翻。
 *
 *  取 `incident.money` 而不是 `total`：这一屏是**银证转账**，它显示的应该是
 *  券商这一侧看得见的那笔划转，不是客户最终要交出去的总数（老陈那一局两者
 *  差着二十万，见 scenario.py 的 `incident_money`）。
 *
 *  取 `incident.hint` 而不是拼 `facts`：那一行本来就是"这笔为什么反常"的
 *  一句话摘要，每个场景各写一份，现成且真。顺带它也终结了原先硬编码的
 *  「持仓已于 09:32 全部卖出」——那一行同时还踩着 T+1（今早卖、今天转，
 *  在券商这边走不通，见 scenario.py 那一批改动）。
 *
 *  **玩家已经签过字就不动。** 开局请求可能比玩家慢，而在拦截面底下改写
 *  face 1 的数字既没人看得见，也可能在他返回时露出破绽。 */
export function paintTransfer() {
  if (!SCENE || !SCENE.incident) return;
  const form = $('transferForm');
  if (!form || form.hidden) return;
  // **局部变量不叫 `money`**：`state.js` 导出了一个同名的 `money`，
  // 而 modules.test.mjs 那道"用了别的模块的名字就得 import"的静态检查
  // 只认顶层声明，函数里的同名 const 会被它当成"用了没导入"，整条变红。
  // 浏览器与沙箱里都不会出问题，但那条检查是白名单式的，改名比放宽它便宜。
  const amount = SCENE.incident.money;
  if (amount) $('transferAmount').textContent = `¥${Number(amount).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (SCENE.incident.hint) $('transferMoves').textContent = SCENE.incident.hint;
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
  // **印的是 `incident.money`，不是 `TOTAL()`。** 两者在四个场景里相等，
  // 老陈那一局不等：他的 total 是三十万，而券商侧只看得见十万——另外二十万
  // 在他自己的银行卡上，投顾看不见，而三十万这个数是这一局要问出来的东西。
  // 取不到就退回 TOTAL()，老场景存档不会因此显示成空白。
  $('openingMoney').textContent = wholeMoney(SCENE.incident.money || TOTAL());
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

/** 开打前那一屏。**只列名字与一句话**，见 KEYS 顶部那段关于 brief / tip 的注释。
 *
 *  **两处都按代词做闸，不是按"画过没有"**（2026-08-31）。原先脚注按
 *  `dataset.ta` 判、七条列表按 `childElementCount` 判，两道闸的判据不一样——
 *  而 `enterGame()` 显示这一屏时**不等开局请求**（见那边的注释：那是有意的，
 *  读这一屏的时间在给「首屏 ≤3 秒」买单）。于是慢网下会走出这么一局：
 *
 *  · 画的时候 `/api/game/start` 还没回来，`SCENE` 是 null，
 *    `peerPronoun()` 退回默认「他」，七条全画成「他」；
 *  · 开局响应到了，`setScene()` 把本局客户换成周淑琴（她）；
 *  · 脚注那道闸认得出代词变了，重刷成「她」；
 *    列表那道闸只问"画过没有"，七条原样留着。
 *
 *  **实测结果是同一屏六行「他」配一行「她」**，而下一屏的输入框写着
 *  「输入你想对她说的话」。这正是 `tests/frontend/pronoun.test.mjs` 开头
 *  逐条列出、要杜绝的那个 bug，从一条静态扫描**看不见**的时序路径回来了：
 *  那份测试扫的是源码里有没有写死「他」，而这里源码是对的，错的是画的时机。
 *
 *  半数场景的客户是「她」（周淑琴／林月娥／顾之然），一半的对局踩得到。
 *  且这一屏**只对首次玩家显示**——也就是从大赛页点进来的那一批。
 *
 *  闸留着不能删（这一屏每局都会进来一次，无谓重画七个 DOM 节点没有意义），
 *  只是判据从"画过没有"换成"跟本局客户对不对得上"。 */
export function paintPrimer() {
  const ta = peerPronoun();

  const foot = $('primerFoot');
  if (foot && foot.dataset.ta !== ta) {
    foot.textContent = withTa('对面是活人写的回应，没有选项可选。你说什么，{ta}就接什么。');
    foot.dataset.ta = ta;
  }

  const list = $('primerList');
  if (!list || list.dataset.ta === ta) return;
  // 重画之前先清空。少这一行，代词一变就是七条追加到七条后面
  list.textContent = '';
  Object.keys(KEYS).forEach((k) => {
    const li = document.createElement('li');
    const name = document.createElement('b');
    name.textContent = KEYS[k].name;
    const desc = document.createElement('span');
    // 这一屏的下一步就是聊天，输入框写着「输入你想对{ta}说的话」。
    // 五个客户里三位是「她」，写死代词在这里当场穿帮
    desc.textContent = withTa(KEYS[k].brief);
    li.append(name, desc);
    list.appendChild(li);
  });
  list.dataset.ta = ta;
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
  // **存档要在这儿落，不能在 loadGame() 里。** 请求一回来就存的话，
  // 玩家还没点开聊天——甚至还在转账确认屏上——sessionStorage 里已经有一份
  // "可续局"的状态。这时候刷新一下，boot() 认得出 savedGame，直接把他
  // 甩进 chat 屏，转账确认与开打前那一屏（primer）全被跳过。
  // 只有真正进了聊天，这份存档才是"续局"该续的东西。
  saveGame();
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
