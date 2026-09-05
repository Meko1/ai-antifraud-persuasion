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
import { game, scoredTurns } from './state.js';
// chat.js 反过来也会 import 本文件的 `syncStuckHints`（每轮打完调一次）。
// 这条环在 ESM 下是安全的，与 `review.js`/`control.js` 那条环同一个理由：
// 两边都只在运行时调对方的函数，模块顶层谁都不碰对方的导出。
import { syncSend } from './chat.js';

// 只从三把「本能」钥匙出内容，理由写在 keys.js 的 STUCK_HINTS 上面
const STUCK_KEYS = Object.keys(STUCK_HINTS);

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

/** 抬头/输入框那一排兜底句的显隐与内容。**只有这一处决定它**
 *  （与 `syncSend`、`control.js` 的 `syncEarlyReview` 同一条规矩）。
 *
 *  两条变体按 `game.turns.length` 的奇偶轮换——不需要像 `app/fallback.py`
 *  那样记完整的排除窗口：这里一次最多连着出现两三次，轮换就够避免
 *  "同一句话又出现了"这种一眼看出来的重复。
 */
export function syncStuckHints() {
  const box = $('stuckHints');
  const row = $('stuckHintsRow');
  if (!box || !row) return;

  if (!shouldShowStuckHints()) {
    box.hidden = true;
    row.textContent = '';
    return;
  }

  const variant = game.turns.length % 2;
  row.textContent = '';
  STUCK_KEYS.forEach((key) => {
    const text = STUCK_HINTS[key][variant];
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
