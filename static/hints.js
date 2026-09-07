/* 卡住时的兜底句——「不知道该说什么」的那条出路（2026-09-04）。
 *
 * ## 为什么加这个
 *
 * `MOOD_HINTS`（keys.js）只给原理——"{ta}在防着你，先让{ta}愿意开口"——
 * 从原理到一句能发出去的话，中间那一步正是新手卡住的地方：知道该干什么，
 * 不知道具体怎么问。`tools/balance_sim.py` 的门槛量过 novice 这一档，
 * 蒙特卡洛 20000 局／人设 20.2% 被拉黑、劝住 0.0%——不是他打得差，
 * 是这一局结构上就学不会（该注释见 `static/keys.js` 顶上 `MOOD_HINTS`
 * 那一段）。
 *
 * ## 为什么不是全程提示
 *
 * `tools/balance_sim.py` 顶部那条警告一直成立：界面把答案印出来，
 * 任何会读字的玩家两轮就滑向 expert，§9.1 那套人群分布当场作废。
 * 这里的触发条件把 expert / speedrun 天然排除在外——他们每局只碰得到
 * 0.6–0.8 次（命中率 0.78 / 0.97），novice 是 2.3 次。蒙特卡洛验过：
 * 给这个兜底不改 expert / speedrun 的胜率，只改 novice 的被拉黑率和
 * 转账档（详情见给出这次改动的那次对话，不重复贴数字）。
 *
 * ## 触发条件
 *
 *   · 一轮都没打过（连第一句都没想好说什么）
 *   · 最近连续两轮，一把钥匙都没命中过
 *
 * 两条都对应"他现在真的卡住了"，不是"他刚好没扎根"或"他这轮说教了但
 * 命中了钥匙"——后两种不该被打断。
 */

import { KEYS, STUCK_HINTS } from './keys.js';
import { $ } from './dom.js';
import { game, scoredTurns, PING } from './state.js';
// chat.js 反过来也会 import 本文件的 `syncStuckHints`（每轮打完调一次）。
// 这条环在 ESM 下是安全的，与 `review.js`/`control.js` 那条环同一个理由：
// 两边都只在运行时调对方的函数，模块顶层谁都不碰对方的导出。
import { syncSend } from './chat.js';

// 只从三把「本能」钥匙出内容，理由写在 keys.js 的 STUCK_HINTS 上面。
// 顺序即句库里的出现顺序：三个筹码是三种不同的动作，摆放次序该是稳定的
const STUCK_KEYS = [...new Set(STUCK_HINTS.map((h) => h.key))];

// 一次给三条。**不是"一把钥匙一条"**——第 1 轮拆矛盾那一把拿不出话来
// （见下面 `stuckHints`），那一格由别把钥匙的下一句补上
const STUCK_COUNT = 3;

function 命中过钥匙(turn) {
  return !!(turn && turn.hits && turn.hits.some((h) => h in KEYS));
}

/** 最近连续几轮一把钥匙都没命中。**用 `scoredTurns()` 不用 `game.turns`**：
 *  分类降级的那一轮 hits 是空的，不是玩家没说到点子上，不该算进这个streak
 *  （理由与 `state.js` 那条同名注释一致）。 */
function 连续未命中轮数() {
  const turns = scoredTurns();
  let streak = 0;
  for (let i = turns.length - 1; i >= 0 && !命中过钥匙(turns[i]); i -= 1) {
    streak += 1;
  }
  return streak;
}

const STUCK_STREAK = 2;

function shouldShowStuckHints() {
  if (game.ending || game.exited) return false;
  if (game.turns.length === 0) return true;
  return 连续未命中轮数() >= STUCK_STREAK;
}

/** 一轮里她说的那几句拼成一段。逐轮记录里 `lines` 才是真正下发过的，
 *  `reply` 是它们连起来的那一份（续局存档两样都有）。 */
function 她这轮说的(turn) {
  if (!turn) return '';
  return (turn.lines && turn.lines.length) ? turn.lines.join('\n') : (turn.reply || '');
}

/** **她刚回的那一句**（一轮都没打过就是开场白）。
 *
 *  `clash` 认的是这一份而不是整局：她五轮前提过"用途"两个字，不该让
 *  "问用途"这类问法从此消失；她**刚刚**把这两个字挡回来，那才是不该撞的。 */
function 她刚说的() {
  return game.turns.length
    ? 她这轮说的(game.turns[game.turns.length - 1])
    : (game.opening || '');
}

/** 她这一局说过的全部话：开场白 + 每轮台词。`cue` 认的是这一份——
 *  她第 2 轮抖出来的「老师」，第 5 轮仍然是可以拿去顶的材料。 */
function 她说过的() {
  return [game.opening || '']
    .concat(game.turns.map(她这轮说的))
    .join('\n');
}

/** 我这一局说出去的全部话：开场那条系统提醒 + 每一轮。
 *
 *  **`PING()` 要算进来**：它是玩家名下的第一条消息，玩家看得见它。 */
function 我说过的() {
  return [PING() || '']
    .concat(game.turns.map((t) => t.utterance || ''))
    .join('\n');
}

/** 这一刻该给哪几句。**不碰 DOM，只读 `game`**——判据全在这儿，
 *  单测直接调它（`tests/frontend/hints.test.mjs`）。
 *
 *  三条判据，来历见 `keys.js` 的 `STUCK_HINTS`：
 *
 *  1. **材料够不够**（`needs`）。拆矛盾要拿她自己说过的话去顶，第 1 轮
 *     她统共说了一句开场白，那一招这会儿不存在——推给玩家，他发出去的
 *     就是一句凭空断言。
 *  2. **撞不撞**（`clash`）。命中她刚顶回来的那句，或我已经问过的话，
 *     就换一条：兜底句是给"想不出词"的人一句能发的话，不是让他把刚被
 *     挡回来的问法原样再问一遍。
 *  3. **接不接得上**（`cue`）。她提过的说法优先——同一把钥匙的几个问法里，
 *     挑那个能接住她原话的。
 *
 *  **档位只加权不筛选**：`moods` 命中加一分，仍然三把钥匙一起给。
 *  按档位把最强的那把挑出来单推，就是 `keys.js` 那条"不按档位挑最优"
 *  的反面——那等于每两轮给玩家发一次答案。
 *
 *  同分的那几条按 `turns.length` 轮着来：连着两次给一模一样的三句，
 *  看着就像攻略在念稿（与 `app/fallback.py` 挡"上一句原样重复"同源，
 *  只是这边不需要记完整的排除窗口）。 */
export function stuckHints() {
  const rounds = game.turns.length;
  const 她说过 = 她说过的();
  const 挡住 = `${她刚说的()}\n${我说过的()}`;

  const 可用 = STUCK_HINTS.filter((h) => {
    if ((h.needs || 0) > rounds) return false;
    if (h.clash && h.clash.test(挡住)) return false;
    // 原样发出去过的那一句，`clash` 不一定罩得住（正则挑的是说法，
    // 不是整句）——挑过一次就别再推第二次
    return !挡住.includes(h.text);
  });

  const 分 = (h) => (h.cue && h.cue.test(她说过) ? 2 : 0)
    + (h.moods && h.moods.includes(game.mood) ? 1 : 0);

  const 挑一句 = (pool) => {
    if (!pool.length) return null;
    const 最高 = Math.max(...pool.map(分));
    const 同分 = pool.filter((h) => 分(h) === 最高);
    return 同分[rounds % 同分.length];
  };

  const picked = [];
  // 先一把钥匙一条：三个筹码摆的是三种不同的动作，不是三句同义的话
  STUCK_KEYS.forEach((key) => {
    const one = 挑一句(可用.filter((h) => h.key === key));
    if (one) picked.push(one);
  });
  // 某一把这会儿一条都拿不出来（第 1 轮的拆矛盾），补满三条再走：
  // 三个筹码是这一排的形状，缺一格不如换一句
  for (const h of 可用) {
    if (picked.length >= STUCK_COUNT) break;
    if (!picked.includes(h)) picked.push(h);
  }
  return picked.slice(0, STUCK_COUNT).map((h) => h.text);
}

/** 抬头/输入框那一排兜底句的显隐与内容。**只有这一处决定它**
 *  （与 `syncSend`、`control.js` 的 `syncEarlyReview` 同一条规矩）。
 */
export function syncStuckHints() {
  const box = $('stuckHints');
  const row = $('stuckHintsRow');
  if (!box || !row) return;

  // 一条都挑不出来时整块收起来，不留一个只有标题的空壳
  const hints = shouldShowStuckHints() ? stuckHints() : [];
  if (!hints.length) {
    box.hidden = true;
    row.textContent = '';
    return;
  }

  row.textContent = '';
  hints.forEach((text) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'stuck-hint-chip';
    btn.textContent = text;
    btn.addEventListener('click', () => pickStuckHint(text));
    row.appendChild(btn);
  });
  box.hidden = false;
}

/** 点一句兜底句：填进输入框，不直接发送。
 *
 *  **不自动发**：这一局判的是玩家自己挑的话，兜底句只兜"想不出词"，
 *  发不发、发之前要不要改一个字，仍然是他的选择——这也是它和"跳过
 *  这一轮"的区别，跳过不存在，这里给的只是一句可以改的草稿。
 */
function pickStuckHint(text) {
  const input = $('say');
  if (!input) return;
  input.value = text;
  syncSend();
  input.focus();
  // 挑了一句之后先收起来，省得跟正在编辑的输入框抢注意力；
  // 没发送就清空重打，下一次 `syncStuckHints()` 该出现还是会出现——
  // 这里不改 `game` 的任何字段，不需要 `saveGame()`
  const box = $('stuckHints');
  if (box) box.hidden = true;
}
