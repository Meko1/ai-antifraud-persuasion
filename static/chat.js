import { $, showScreen, sleep, thread } from './dom.js';
import { BREACHES, MOODS, MOOD_HINTS } from './keys.js';
import { postTurn, readEvents } from './api.js';
import { openReview } from './review.js';
import {
  ERRORS, PAYEE, PING, SCENE, endingMeta, game, money, peerInitial,
  peerPronoun, pressureNote, saveGame, setScene, startNewClient,
} from './state.js';
import { paintClientPicker, paintDesk, paintOpening } from './opening.js';

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
    // `{ta}` 换成本局客户的代词。五个场景里三位是「她」，写死「他」的话
    // 这一行会和两行之下的输入框（「输入你想对她说的话」）当场打架。
    const hint = (MOOD_HINTS[mood] || MOOD_HINTS.guarded).replaceAll('{ta}', peerPronoun());
    if (hintEl.textContent !== hint) hintEl.textContent = hint;
  }

  // 细条不带数字也不闪：它只是个余光里的东西，用来兜住"完全没有反馈"的茫然
  const pct = Math.max(0, Math.min(100, game.trust));
  const fill = $('trustFill');
  fill.style.width = pct + '%';
  fill.className = 'st-fill' + (pct < 20 ? ' danger' : pct < 45 ? ' low' : '');
  $('trustGoal').style.left = game.threshold + '%';
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

export async function playTurn(utterance) {
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
        $('turnCurrent').textContent = String(round);
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
  }

  saveGame();
  if (game.ending) return finish();

  game.busy = false;
  $('say').disabled = false;
  syncSend();
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
