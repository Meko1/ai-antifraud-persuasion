/* 「同一句话，换个时候说」这一块的**取数**，不含任何渲染。
 *
 * ## 为什么单独一个模块
 *
 * 这块判据现在有两个消费方：复盘正文（`review.js` 的 `paintContrast`）
 * 与分享卡（`history.js` 的 `makeCard`，2026-08-29 起卡面主角就是它）。
 * 两边算同一件事，**但不能各算一套**——两个地方对同一局给出不同的倍数，
 * 是这个仓库最不能出的那种错：它直接打在"判分是可复现的"这条主张上。
 *
 * 放新模块而不是塞进 `review.js`，是因为依赖方向：`review.js` 已经
 * `import` 了 `history.js`（`makeCard`），反过来再 import 就成环了。
 * 这个模块只依赖 `keys.js` 与 `state.js`，两个消费方都在它下游。
 *
 * ## 它选哪一轮讲
 *
 * 四条分支，优先级从上到下，与 2026-08-25 那次修 P0-4 时定的一样
 * （原文与理由留在 `review.js` 的 `paintContrast` 注释里）：
 *
 *   mistimed —— 有"给了依据也下了判断、但时候不对"的那一轮，优先讲它
 *   gap      —— 否则讲"动作对、时候差得最远"的那一轮（落差 > 0.15 才算）
 *   flat     —— 时机都挑得不错：讲他用得最多的那一把，把四档落差摊开
 *   nokey    —— 一把都没命中：拿这个场景落差最大的那一把举例
 *
 * 后两条是兜底，**不编任何评价**，只把服务端下发的矩阵里本来就有的数字
 * 念出来。宁可少说，也不能说得比证据多。
 */

import { KEYS, MOODS } from './keys.js';
import { game, scoredTurns } from './state.js';

/** 一把钥匙在四档里的最高 / 最低。用来说"同一句话差多少倍"。 */
const peak = (row) => Object.entries(row).sort((a, b) => b[1] - a[1])[0];
const floorOf = (row) => Object.entries(row).sort((a, b) => a[1] - b[1])[0];

/** 倍数。分母是 0 就返回 null——不印一个 `Infinity` 上去。 */
function ratio(big, small) {
  return small > 0 ? (big / small).toFixed(1) : null;
}

/**
 * 这一局「同一句话，换个时候说」讲哪一条。
 *
 * **拿不到效力矩阵就返回 null**（老令牌、结局事件没下发）——两个消费方
 * 各自决定怎么退：复盘整块不出现，分享卡退回旧版式。空壳一律不留。
 *
 * @returns {null | {
 *   kind: 'mistimed'|'gap'|'flat'|'nokey',
 *   key: string, name: string,
 *   quote: string, round: number|null,
 *   mood: string|null, val: number|null,
 *   bestMood: string, bestVal: number,
 *   worstMood: string|null, worstVal: number|null,
 *   times: string|null,
 * }}
 */
export function contrastFacts() {
  const eff = game.ending && game.ending.efficacy;
  if (!eff) return null;

  const rowOf = (k) => eff[k] || null;
  const nameOf = (k) => (KEYS[k] ? KEYS[k].name : k);
  const turns = scoredTurns();

  // 一 · 空口断言那一轮：他给了依据也下了判断，只是时候不对
  const mistimed = turns.find((t) => t.mistimedWarning);
  if (mistimed && rowOf('informed_warning')) {
    const [bestMood, bestVal] = peak(rowOf('informed_warning'));
    return {
      kind: 'mistimed',
      key: 'informed_warning',
      name: nameOf('informed_warning'),
      quote: mistimed.utterance,
      round: mistimed.round,
      mood: mistimed.judgedMood,
      val: null,
      bestMood,
      bestVal,
      worstMood: null,
      worstVal: null,
      times: null,
    };
  }

  // 二 · "动作对、时候不对"差得最远的那一轮
  let worst = null;
  turns.forEach((t) => {
    if (t.efficacy == null) return;
    const k = (t.hits || []).find((h) => rowOf(h));
    if (!k) return;
    const [bestMood, bestVal] = peak(rowOf(k));
    const gap = bestVal - t.efficacy;
    if (!worst || gap > worst.gap) worst = { t, k, bestMood, bestVal, gap };
  });
  if (worst && worst.gap > 0.15) {
    const { t, k, bestMood, bestVal } = worst;
    return {
      kind: 'gap',
      key: k,
      name: nameOf(k),
      quote: t.utterance,
      round: t.round,
      mood: t.judgedMood,
      val: t.efficacy,
      bestMood,
      bestVal,
      worstMood: null,
      worstVal: null,
      times: ratio(bestVal, t.efficacy),
    };
  }

  // 三、四 · 兜底：不讲某一轮，讲这张表本身
  const used = {};
  turns.forEach((t) => {
    (t.hits || []).forEach((h) => { if (rowOf(h)) used[h] = (used[h] || 0) + 1; });
  });
  const favourite = Object.entries(used).sort((a, b) => b[1] - a[1])[0];
  // 没命中过就挑这个场景里落差最大的那一把，那是这张表最能说明问题的一格
  const pick = favourite ? favourite[0] : Object.keys(eff).sort((a, b) => {
    const spread = (k) => peak(eff[k])[1] - floorOf(eff[k])[1];
    return spread(b) - spread(a);
  })[0];
  const row = pick && rowOf(pick);
  if (!row) return null;

  const [bestMood, bestVal] = peak(row);
  const [worstMood, worstVal] = floorOf(row);
  return {
    kind: favourite ? 'flat' : 'nokey',
    key: pick,
    name: nameOf(pick),
    quote: '',
    round: null,
    mood: MOODS[game.mood] ? game.mood : null,
    val: null,
    bestMood,
    bestVal,
    worstMood,
    worstVal,
    times: ratio(bestVal, worstVal),
  };
}
