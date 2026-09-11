import { $, REDUCED, buzz, chime, showScreen, sleep, thread } from './dom.js';
import { BREACHES, KEYS, MOODS, MOOD_HINTS } from './keys.js';
import { startersFor } from './starters.js';
import { postTurn, readEvents } from './api.js';
import { openReview } from './review.js';
import {
  ERRORS, PAYEE, PING, SCENE, endingMeta, game, money, peerInitial,
  pressureNote, saveGame, setScene, startNewClient, withTa,
} from './state.js';
import { paintClientPicker, paintDesk, paintOpening } from './opening.js';
import { endEarly, syncEarlyReview } from './control.js';

// ── 对话 ────────────────────────────────────────────────────

export function toBottom() {
  thread.scrollTop = thread.scrollHeight;
}

export function avatar(who) {
  const el = document.createElement('span');
  el.className = 'avatar' + (who === 'them' ? ' av-chen' : '');
  el.setAttribute('aria-hidden', 'true');
  el.textContent = who === 'them' ? peerInitial() : '我';
  return el;
}

export function msgRow(who, text) {
  const row = document.createElement('div');
  row.className = `msg ${who}`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  row.append(avatar(who), bubble);
  return row;
}

export function say(who, text) {
  thread.appendChild(msgRow(who, text));
  toBottom();
}

/** 「对方正在输入」的那个气泡。他说完的每一句都插在它前面。 */
export function typingRow() {
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
export function paced(anchor) {
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

export function divider(text) {
  const el = document.createElement('div');
  el.className = 'timedivider';
  el.textContent = text;
  thread.appendChild(el);
}

/** 系统提示。微信用它显示「你撤回了一条消息」，判分卡走同一个位置。 */
export function sysnote(nodes, bad) {
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

/** 剧情旁白。王老师在群里催的那一下走这个位置——
 *  它不是判分反馈，是一个看得见的施压来源。 */
export function narrate(text) {
  sysnote([text]).classList.add('push');
}

/* ── 第一轮候选开场句（2026-09-11 加）──────────────────────────────────
 *
 * **只填输入框，不直接发送**——点一下把这句话搬进 `#say`，玩家还能删、
 * 还能改、还能不点直接自己打字。这不是选择题：选择题是"点了就等于说了
 * 这句话"，这里点了只是把话搬到了他自己那支笔下面。
 *
 * 只在第一轮出现，理由与内容来源见 `starters.js` 顶部那段长注释。
 */
const STARTER_ID = 'starterChips';

export function paintStarters(sid) {
  const list = startersFor(sid);
  if (!list.length) return;
  const wrap = document.createElement('div');
  wrap.id = STARTER_ID;
  wrap.className = 'starter-chips';
  wrap.setAttribute('role', 'group');
  wrap.setAttribute('aria-label', '开场句参考，点一下填进输入框');
  list.forEach((s) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'starter-chip';
    btn.textContent = s.text;
    btn.addEventListener('click', () => {
      const input = $('say');
      input.value = s.text;
      input.focus();
      syncSend();
    });
    wrap.appendChild(btn);
  });
  thread.appendChild(wrap);
  toBottom();
}

/** 第一轮一旦发出去就清掉——从第二轮起，对方已经说过话了，
 *  再给候选句就是真正的泄题，这份表的使用期限只到这里为止。 */
export function clearStarters() {
  document.getElementById(STARTER_ID)?.remove();
}

export function paintMood(mood) {
  const el = $('mood');
  const word = MOODS[mood] || MOODS.guarded;
  if (el.textContent !== word) {
    el.textContent = word;
    el.classList.remove('turn');
    void el.offsetWidth;  // 强制重排，让同名动画能第二次播
    el.classList.add('turn');
  }

  // 时机线索。**只说他现在的状态意味着什么，不报该用哪一把钥匙**——
  // 理由写在 keys.js 的 MOOD_HINTS 上面。节点缺失时静默跳过：
  // 它是辅助信息，不该因为少一个 id 就把这一轮的渲染整个掀掉。
  const hintEl = $('moodHint');
  if (hintEl) {
    // `{ta}` 换成本局客户的代词。客户里「她」占一半，写死「他」的话
    // 这一行会和两行之下的输入框（「输入你想对她说的话」）当场打架。
    //
    // 2026-08-30 改走 `withTa()`：这一处原是全仓唯一走占位符的地方，
    // 而那一批把 KEYS / 复盘 / 分享卡全接上来之后，得有一个统一出口——
    // 静态测试认的就是它（`tests/frontend/pronoun.test.mjs`）。
    const hint = withTa(MOOD_HINTS[mood] || MOOD_HINTS.guarded);
    if (hintEl.textContent !== hint) hintEl.textContent = hint;
  }
}

// ── 一轮 ────────────────────────────────────────────────────

/** 发送键的可用状态。**只有这一个地方决定它**。
 *
 *  提交那一路本来就有 `if (!text) return`，所以空输入从来没真的发出去过——
 *  问题是按钮**看上去能按，按了却一点反应都没有**。一个按下去什么都不发生
 *  的控件，比一个明确禁用的控件更难懂：玩家不知道是自己没按对，还是程序坏了。
 *
 *  别在别处直接写 `disabled = false`：那会在"正忙"或"输入框是空的"时候
 *  把它放开。这个函数存在的全部理由就是把那两个条件收在一处。
 */
export function syncSend() {
  const input = $('say');
  const send = $('send');
  if (!input || !send) return;
  send.disabled = game.busy || !input.value.trim();
}

/* ── 非泄题即时反馈（2026-09-11 加，GPT复核报告 P0-3）───────────────────
 *
 * 报告原文举的例子是"他停顿了几秒""他开始解释而不是直接反驳"——这两句
 * 这个仓库给不出：没有停顿时长这个数据，也没有"解释 vs 反驳"这个分类，
 * 编一句听着真实但没有数据支撑的台词，是这个仓库明确不做的事
 * （opening.js 那句"这个仓库不许对玩家断言一件没发生的事，哪怕它多半
 * 发生了"，同一条原则）。
 *
 * 能诚实给的只有一件事：**这一轮信任度净变了多少**（`trust - before`），
 * 这是真算出来的数，不是编的。但净变化本身已经很接近"分数"了——
 * 所以只取**方向**（起色/没起色），不读出具体数字，且只在变化幅度
 * 够不上"情绪档位跟着换了一档"（那部分 `paintMood` 已经在报）的时候才
 * 单独说一句，避免和抬头那颗情绪徽章说重复的话。
 *
 * 第一轮不走这条路——它已经有自己的戏剧化亮相（上面那段 `showRevealStage`），
 * 两个提示挤在同一轮会互相抢戏。
 */

/** 「净变化够不上一档」的阈值。**不是精确算出来的**（app/scoring.py 没有
 *  为"多大算一档"单独定义一个数，档位切换本身由 `mood_for(trust)` 的
 *  分段决定），这里给的是一个保守估计——宁可漏判几次真实的小起色，
 *  也不要在噪声量级的波动上也念一句，把这条反馈变成每轮必有的背景音。 */
const FEEDBACK_DELTA_FLOOR = 3;

function turnFeedbackNote(prevMood) {
  const last = game.turns[game.turns.length - 1];
  if (!last) return null;
  // 情绪档位已经变了：`#mood` 那颗徽章自己会播一次「turn」动画，
  // 这里再说一遍是同一件事说两次。**判据是这一轮结束后的档位
  // （`game.mood`，调用这个函数之前已经被 `score.mood` 重新赋值过）
  // 对比这一轮开始前的档位（`prevMood`）**——`last.judgedMood` 是
  // 分类器判这一轮用的档位，取的是轮次**开始前**那一刻，跟 `prevMood`
  // 几乎总是同一个值，拿它俩比较等于这道闸永远不关，是踩过一次的错。
  if (game.mood !== prevMood) return null;
  const delta = last.trust - last.before;
  if (Math.abs(delta) < FEEDBACK_DELTA_FLOOR) return null;
  // {ta} 占位符：客户里「她」占一半，写死「他」在这儿会当场穿帮——
  // 这条规矩全仓统一，见 keys.js 末尾 MOOD_HINTS 上面那段
  return withTa(delta > 0 ? '这句，{ta}听进去了一点。' : '这句，{ta}没听进去。');
}

/* ── 命中钥匙的信心提示（2026-09-11 加，所有者明确要求）─────────────────
 *
 * 比上面 `turnFeedbackNote()` 更明确的一条：不是"净变化的方向"，是
 * "这一轮有没有命中七把钥匙里的一把"，命中就给一句肯定，**不点名哪把**。
 *
 * 这条比 `turnFeedbackNote()` 更接近红线——它确认的是"一个具体的机制
 * 刚刚触发了"，不只是"结果往好的方向走了一点"，更容易被反复试探摸出
 * "什么样的话会触发这句话"。所有者在访谈里被明确告知这层风险之后，
 * 选的仍然是这一版（对照选项是"维持现状不新增这个信号"），所以照这版做，
 * 但止步于"命中过"这一件事——不点名具体哪把钥匙、不给分值、不给效力
 * 矩阵，那几样仍然只留在复盘里。
 *
 * 优先级高于 `turnFeedbackNote()`：同一轮命中钥匙、净变化又恰好过线，
 * 两句话挤一起是噪音，命中钥匙是更确定的信号，该赢的是它。
 */
function keyHitNote() {
  const last = game.turns[game.turns.length - 1];
  if (!last) return null;
  const hit = (last.hits || []).some((h) => h in KEYS);
  if (!hit) return null;
  return withTa('这一下，{ta}听进去了——这句说到点子上了。');
}

/* ── 第一轮之后的戏剧化亮相（2026-09-11 加）─────────────────────────────
 *
 * GPT 复核报告 P0-2 想要的是把"同一句话换个时候说，效力差几倍"从十轮后的
 * 复盘提前到第一句话之后。**这个仓库做不出报告原文那句话**（"现在说只有
 * 0.6倍，他动摇时是1.2倍"），不是不想做，是两条更早的规矩挡在前面，
 * 不能假装没看见就写过去：
 *
 * 一、**判分卡不在对局中出现**（POSITIONING「不做什么」、下面 `playTurn`
 * 那段注释原话）。标签与分数一律留到复盘——"边打边给答案，玩家两轮就学会
 * 照着清单刷分，不再读人"。这条红线不分是第几轮，第一轮同样管，而且
 * 这条已经在这次改版的访谈里明确confirm过不松动。
 *
 * 二、**那份数据本身要等 `ending` 才存在**。"同一把钥匙在别的情绪档位值
 * 多少倍"是 `game.ending.efficacy`（见 `contrast.js` `contrastFacts()`
 * 开头那道闸），只在真正的结局事件里下发；第一轮更不可能有——
 * `decide_ending()`（app/scoring.py）只在信任度冲过 80、跌破拉黑线，或者
 * 撑到第 10 轮这三种情况下才给结局，一句话不可能提前触发这三条中的任何
 * 一条。`endEarly()` 提前退出同样拿不到它——那条路走的是 `unfinished`，
 * 服务端从没发过 `ending` 事件。
 *
 * 能提前、且不碰这两条线的，只有**已经在对局中公开展示的东西**——
 * 情绪档位与时机线索（`#mood`、`#moodHint`，`paintMood()` 每轮都在刷新
 * 它们，不是新数据）。这次改动做的是把这份本来摆在屏幕一角的信息，在
 * 第一轮之后单独拿出来、放大、断网感地演一遍：把"这局在读你说话的时机"
 * 这件事的分量提前立住，不是提前泄题——是把已经公开的信息重新排一次版。
 */

const REVEAL_KEY = 'aap.reveal.seen';

function revealSeen() {
  try { return localStorage.getItem(REVEAL_KEY) === '1'; } catch { return false; }
}

function markRevealSeen() {
  try { localStorage.setItem(REVEAL_KEY, '1'); } catch { /* 存不了就每局都演一遍，不致命 */ }
}

/** 跳出手机边框的那一下。**挂在 `document.body` 上，不挂在 `.app-shell`
 *  里**——`.app-shell` 自己 `overflow: hidden`，挂在里面会被裁成一个
 *  贴在手机屏幕内的方块，"跳出边框"这件事就没发生。它要盖住的是整块
 *  桌面舞台（`static/style.css` 里"桌面舞台"那一段新加的深色画布），
 *  不只是手机屏幕那一小块。 */
/** 跳出手机边框那一下的通用壳。两个调用点共用（第一轮揭晓、结束体验局
 *  时的收束），差的只是文案、音效方向、要不要读秒——结构、无障碍处理、
 *  低动态偏好豁免只写这一份，不许两份各写各的然后走散。 */
function showFullscreenStage({
  ariaLabel, eyebrow, moodWord, hint, thesis, cta, chimeNotes, chimeGain,
  vibrate, autoDismissMs,
}) {
  if (vibrate) buzz(vibrate);
  if (chimeNotes) chime(chimeNotes, chimeGain);
  return new Promise((resolve) => {
    const stage = document.createElement('div');
    stage.className = 'reveal-stage';
    stage.setAttribute('role', 'dialog');
    stage.setAttribute('aria-label', ariaLabel);
    stage.innerHTML = `
      <div class="reveal-card">
        <p class="reveal-eyebrow">${eyebrow}</p>
        ${moodWord ? '<p class="reveal-mood-word"></p>' : ''}
        ${hint ? '<p class="reveal-hint"></p>' : ''}
        <p class="reveal-thesis">${thesis}</p>
        <button type="button" class="reveal-continue">${cta}</button>
      </div>`;
    if (moodWord) stage.querySelector('.reveal-mood-word').textContent = moodWord;
    if (hint) stage.querySelector('.reveal-hint').textContent = hint;
    document.body.appendChild(stage);

    let done = false;
    const dismiss = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (REDUCED) { stage.remove(); resolve(); return; }
      stage.classList.add('leaving');
      stage.addEventListener('animationend', () => { stage.remove(); resolve(); }, { once: true });
    };
    stage.querySelector('.reveal-continue').addEventListener('click', dismiss);
    // 点空白处也能走，和这个仓库其余抽屉/弹层的规矩一致——不设强制阅读。
    stage.addEventListener('click', (e) => { if (e.target === stage) dismiss(); });
    // 低动态偏好下不自动读秒，但仍然要有人能关掉它，交给按钮
    const timer = REDUCED || !autoDismissMs ? null : setTimeout(dismiss, autoDismissMs);
    stage.querySelector('.reveal-continue').focus({ preventScroll: true });
  });
}

function showRevealStage(mood) {
  return showFullscreenStage({
    ariaLabel: '时机判定',
    eyebrow: '妙想 · 正在判读时机',
    moodWord: MOODS[mood] || MOODS.guarded,
    hint: withTa(MOOD_HINTS[mood] || MOOD_HINTS.guarded),
    thesis: '这局判的不只是你说了什么，还有你挑的时候对不对——同一句话，'
      + '换个时候说，效果会完全不一样。',
    cta: '继续对话',
    // 两声上行（G5 → C6），跟 `opening.js` 的 `alarmFeedback()` 反过来——
    // 那一下是"被摁住了"要读下行，这一下是"亮出一件事"，读上行
    chimeNotes: [[784, 0], [1046.5, .1]],
    chimeGain: .045,
    vibrate: [16, 40, 16],
    autoDismissMs: 4200,
  });
}

/** 点「结束并看复盘」之后，先给一眼能看完的收束，再落进完整复盘页——
 *  不是把完整复盘页做轻，是在它前面加一层"先接住这一局"的过渡，呼应
 *  GPT复核报告 P1-3 想要的"短体验结束后立即生成轻量结果卡"。
 *
 *  **内容一个字不超出已经安全展示过的信息**：情绪档位（`#mood`本来就在
 *  展示）、这是主动结束（对局本身就看得见）。不新增任何数字、标签，
 *  不趁机塞一个之前没有过的判分维度。 */
function showQuickFinishStage() {
  return showFullscreenStage({
    ariaLabel: '这一局先到这儿',
    eyebrow: '妙想 · 这一局先到这儿',
    moodWord: MOODS[game.mood] || MOODS.guarded,
    thesis: withTa('这只是这一局的一个切面——完整复盘里还有逐轮证据和K线，'
      + '能看出{ta}最后为什么停在这一档。'),
    cta: '看完整复盘',
    // 两声下行，跟揭晓那两声反过来——这次读的是"收一下"，不是"亮出来"
    chimeNotes: [[659.3, 0], [523.3, .12]],
    chimeGain: .04,
    vibrate: 18,
  });
}

const KEYS_HINT_KEY = 'aap.keyshint.seen';

function keysHintSeen() {
  try { return localStorage.getItem(KEYS_HINT_KEY) === '1'; } catch { return false; }
}

function markKeysHintSeen() {
  try { localStorage.setItem(KEYS_HINT_KEY, '1'); } catch { /* 存不了就每局都演一遍，不致命 */ }
}

/** 「钥匙」按钮的一次性提示脉冲。**只在这台设备演一次**，理由与实现见
 *  `static/style.css` 里 `.pulse-hint` 那段长注释——跳过 primer 屏之后，
 *  这颗按钮是七把钥匙唯一的入口，得先让人看见它才谈得上"随时可查"。
 *
 *  放在第一轮揭晓收起来之后：不和那次全屏的戏剧化时刻抢注意力。 */
function pulseKeysButton() {
  if (keysHintSeen()) return;
  markKeysHintSeen();
  const btn = $('openMethods');
  if (!btn) return;
  btn.classList.add('pulse-hint');
  const clear = () => btn.classList.remove('pulse-hint');
  btn.addEventListener('click', clear, { once: true });
  setTimeout(clear, 5000);
}

/** 只在这台设备的第一轮之后演一次。**判据是 `game.turns.length === 1`**，
 *  不是"这局第一次调用"——续局重画（`resumeGame`）不经过 `playTurn`，
 *  不会重复触发；同一局同一轮重试（`rollback` 之后重发）会话未推进，
 *  这个函数根本不会被这条件命中第二次。 */
function maybeShowFirstTurnReveal() {
  if (game.turns.length !== 1 || revealSeen()) return null;
  markRevealSeen();
  return showRevealStage(game.mood);
}

/* ── 第一轮之后，正面邀请"现在结束看结果"（2026-09-11 加）─────────────
 *
 * 大赛体验局改版最初想做一个到点即停的短版本，后来收窄成"默认路径本身
 * 变快"（见 opening.js「快速进场」那段注释），十轮上限没有变——但改窄
 * 之后漏了一件事：**没有任何一处正面邀请"现在结束也可以"**。退出那条路
 * 还在（`control.js` 的 `openExitSheet()`），但要点"退出"才找得到，
 * 而且框的是"离开"，不是"这就是你想要的那个短体验"。
 *
 * 这一条不新建状态机、不改判分：点了就是调 `endEarly()`，跟退出抽屉里
 * "就到这儿，看复盘"那一项完全同一个函数、同一份行为（已打的轮次照常
 * 判分，不编造资金结局）。**每一局的第一轮都会出现**，不是只演一次的
 * 教学提示——"要不要现在就看结果"是每一局都成立的真问题，跟只演一次的
 * `showRevealStage()` 不是同一类东西，不共用那把 localStorage 的锁。
 */
function offerQuickFinish() {
  const p = sysnote([
    '这一轮就想看看效果？现在结束也能看复盘，已打的轮次照常判分。',
  ]);
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = '结束并看复盘';
  btn.className = 'quick-finish-btn';
  btn.addEventListener('click', async () => {
    await showQuickFinishStage();
    endEarly();
  });
  p.appendChild(btn);
}

export async function playTurn(utterance) {
  game.busy = true;
  $('send').disabled = true;
  $('say').disabled = true;
  // 第一轮一发出去就清掉候选开场句——从第二轮起对方已经说过话了，
  // 这份表的使用期限到这里为止（见 `clearStarters()` 的文档）。
  clearStarters();

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
    resp = await postTurn(game.token, utterance);
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
        /* 抬头那个数说的是**接下来要打的那一轮**，不是刚打完的那一轮
         * （2026-08-31 改）。
         *
         * 服务端 `meta.round` 给的是刚打完的序号。原先直接印它，于是玩家
         * 发完第 1 句、正在想第 2 句的时候，抬头写着「第 1 轮 / 12」，
         * 而旁边的格子已经掉到 11——**1 + 11 = 12，同一行自相矛盾**。
         *
         * 而这个仓库里另外两条路早就是"下一轮"的算法了：`resumeGame` 写的是
         * `Math.min(已打 + 1, maxRounds)`，`rollback` 写的是
         * `maxRounds - remaining + 1`。**刷新一下页面数字就跳一格**，
         * 三条路对不上的时候，对的是那两条：玩家盯着这个数是为了知道
         * 「我还能说几次」，不是为了回顾刚才那次。
         *
         * 用 remaining 而不是 `round + 1` 表达，是为了和 rollback 逐字一致——
         * 那两处但凡写法不同，下次改一处漏一处。 */
        const 下一轮 = Math.min(
          game.maxRounds, Math.max(1, game.maxRounds - ev.data.remaining + 1));
        $('turnCurrent').textContent = String(下一轮);
        $('roundFill').style.width = `${(round / game.maxRounds) * 100}%`;
      } else if (ev.name === 'sentence') {
        spoken.push(ev.data.text);
        queue.push(ev.data.text);
      } else if (ev.name === 'score') {
        score = ev.data;
      } else if (ev.name === 'ending') {
        game.ending = ev.data;
        // 揭晓数据在这一刻才到（P1-14）：收款方与试水金额开局不下发，
        // 因为**骗局的名字就写在收款方里**（「转账给 启航财经-王」），
        // 而"这是个什么局"正是这一局要挖的东西。
        // 合进 SCENE，下面 `receipt()` 与结算金额那几处的取值方式一行不用改。
        if (ev.data.money && SCENE) Object.assign(SCENE.money, ev.data.money);
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
    // 这一轮开始前的档位，喂给下面 `turnFeedbackNote()`——它只在档位
    // 没跟着换的时候才开口，档位换了自有 `#mood` 那颗徽章的动画去说。
    const prevMood = game.mood;
    game.trust = score.trust;
    // **game.mood 必须跟着走。** 在这一行之前它只在开局被赋值过一次，
    // 之后十二轮一直是第 1 轮那个值——两处因此都是错的：
    // 续局时 `resumeGame` 的 `paintMood(game.mood)` 画的是开局档位
    // （打到第 8 轮刷新一下，抬头会退回"烦躁"），
    // 复盘也拿不到"他最后停在哪一档"。
    game.mood = score.mood;
    paintMood(game.mood);
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
    if (score.pressure) narrate(pressureNote());
    // 第一轮走的是戏剧化亮相（下面那段），不在这儿重复；第二轮起，
    // 命中钥匙的确认优先于方向性反馈——两句话只留一句，见 `keyHitNote()`
    // 顶部那段注释
    if (game.turns.length > 1) {
      const hit = keyHitNote();
      const note = hit || turnFeedbackNote(prevMood);
      if (note) sysnote([note]);
      // 单音，比揭晓那两声更轻——命中钥匙每局能响好几次，
      // 声音分量要压得比"只演一次"的揭晓更低，不然十轮打下来是噪音
      if (hit) chime([[1318.5, 0]], .035);
    }
  }

  saveGame();
  if (game.ending) return finish();

  // 第一轮之后、放开输入框之前——见上面那段「跳出手机边框」的长注释，
  // 讲的是为什么这里只演情绪与时机线索，不演任何数字。
  const reveal = maybeShowFirstTurnReveal();
  if (reveal) { await reveal; pulseKeysButton(); }
  // 揭晓演过没演过都要出现——它是每局都成立的真问题，不是教学提示
  if (game.turns.length === 1) offerQuickFinish();

  game.busy = false;
  $('say').disabled = false;
  syncSend();
  // 抬头那颗「看复盘」够轮数就露出来——判据是 game.turns.length，
  // 每一轮真正记进 game.turns 之后都要重算一次
  syncEarlyReview();
  $('say').focus();
}

export function failTurn(code) {
  sysnote([ERRORS[code] || ERRORS.internal], true);
  game.busy = false;

  // 这两种都没法接着打：令牌要么过期了，要么已经被消费掉。给一条出路，
  // 别让玩家对着一个再也发不出去的输入框反复试。
  if (code === 'invalid_state' || code === 'replayed') {
    $('composer').hidden = true;
    const again = document.createElement('button');
    again.textContent = '重开一局';
    again.onclick = () => startNewClient();  // 清存档再重载，别把这一局又续回来
    sysnote([code === 'replayed' ? '这一局没法接着打了' : '这一局放得太久了', again], true);
    return;
  }
  $('say').disabled = false;
  syncSend();
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
export function receipt(state, amount) {
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
export function photo(node) {
  const row = document.createElement('div');
  row.className = 'msg them';
  const bubble = document.createElement('div');
  bubble.className = 'bubble photo';
  bubble.appendChild(node);
  row.append(avatar('them'), bubble);
  thread.appendChild(row);
  toBottom();
}

export async function finish() {
  const kind = game.ending.kind;
  $('composer').hidden = true;
  // `playTurn` 早退到这里之前从没走到过下面这句（`if (game.ending)
  // return finish()` 挡在它前面），所以抬头那颗「看复盘」要在这儿自己
  // 收一次——否则打满整局的最后一轮，它会带着上一轮的状态留在已经隐藏的
  // 输入框里。
  syncEarlyReview();

  // 最后几句和前面十二轮一样，一句一个气泡。结局不该是"突然弹出一整段"
  for (const [i, line] of (game.ending.lines || []).entries()) {
    if (i) await sleep(260 + Math.random() * 160);
    say('them', line);
  }
  await sleep(400);

  // 结局揭晓那一下的触感反馈。**四档结局用同一个震动模式**，不按好坏分——
  // 结局是一道阶梯，不是胜负（POSITIONING「不做什么」），差异化的震动
  // 强弱等于在暗示"这个结局该庆祝、那个该沮丧"，和"不做成输赢"是同一条线。
  // 低动态偏好下跳过，与 `opening.js` 的 `alarmFeedback()` 同一条道理：
  // 设了这条的人要的是别惊动我。
  buzz(40);

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

/** 把存下来的那一局重新画出来。
 *
 *  **只重画玩家真看见过的东西**：他说的、对方回的、踩过的合规红线。
 *  判分卡本来就不在对局中出现，所以这里不存在"少画了什么"的问题——
 *  这也是这个作品能低成本续局的原因：对局中的界面本来就极薄。
 *
 *  施压旁白不重画：它是"刚刚发生了一件事"，补在历史里会变成一句
 *  时态不对的话。
 */
export function resumeGame(saved) {
  setScene(saved.scene);
  Object.assign(game, saved.game);
  game.entered = true;

  // 那句告知也存了一份：留存开着和关着说的不是同一句话，
  // 续局时重新拿服务端那句拿不到，用静态兜底会说错（ADR-0006）
  if (saved.notice) {
    document.querySelectorAll('.strangertip').forEach((el) => {
      el.textContent = saved.notice;
    });
  }
  paintDesk();
  paintOpening();

  const 已打 = game.turns.length;
  const 当前轮 = Math.min(已打 + 1, game.maxRounds);
  $('turnTotal').textContent = String(game.maxRounds);
  $('turnCurrent').textContent = String(当前轮);
  $('roundFill').style.width = `${(当前轮 / game.maxRounds) * 100}%`;
  $('remaining').textContent = String(game.remaining);
  paintMood(game.mood);
  // 续局回来抬头那颗「看复盘」要按存档里的状态重算——不写在这儿的话，
  // 打到第 6 轮存的档，刷新回来会先按"0 轮"画一次再等下一轮才补上
  syncEarlyReview();

  divider('下午 2:47');
  say('me', PING());
  say('them', game.opening);
  game.turns.forEach((t) => {
    say('me', t.utterance);
    const lines = (t.lines && t.lines.length) ? t.lines : [t.reply];
    lines.forEach((line) => { if (line) say('them', line); });
    if (t.breached) {
      const names = (t.hits || [])
        .filter((h) => h in BREACHES).map((h) => BREACHES[h].name);
      if (names.length) {
        sysnote([`合规红线 · ${names.join(' / ')}`, '真实展业中这句要留痕'], true);
      }
    }
  });

  showScreen('chat');

  if (game.ending) {
    // 打完之后才刷新的：把结局那几句补回去，直接开复盘。
    // **不重播那段逐句动画**——他已经看过一次了，再演一遍是在浪费他的时间
    (game.ending.lines || []).forEach((line) => say('them', line));
    $('composer').hidden = true;
    openReview();
  } else if (game.exited) {
    // 他主动结束过，然后刷新了。**退出这个决定不该被一次刷新撤销**——
    // 复盘照旧走 unfinished 那一档，不编造资金结局
    $('composer').hidden = true;
    openReview();
  } else {
    syncSend();   // 续局回来输入框是空的，发送键就该是灰的
    $('say').focus();
  }
  paintClientPicker();
}
