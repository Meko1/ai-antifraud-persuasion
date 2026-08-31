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
import { game, peerPronoun, scoredTurns } from './state.js';

/** 一把钥匙在四档里的最高 / 最低。用来说"同一句话差多少倍"。 */
const peak = (row) => Object.entries(row).sort((a, b) => b[1] - a[1])[0];
const floorOf = (row) => Object.entries(row).sort((a, b) => a[1] - b[1])[0];

/* ── 那个"更好的时候"，在这一轮之前还是之后（2026-08-31 补）──────────────
 *
 * `bestMood` 只是效力矩阵那一行的 argmax，**它跟这一局的时间线毫无关系**。
 * 而两个消费方都把它写成了将来时：「同一句话，等{ta}烦躁再说，值 1.9×」。
 *
 * 实测一局：第 6 轮「支持自主」，他当时松动（1.2×），bestMood 是烦躁（1.9×）。
 * 可他**只在第 1 轮之前烦躁过**——玩家第一句就把他从烦躁里推出来了。
 * 于是复盘让他"等"一个五轮前就已经过去、而且是他自己亲手推走的状态。
 *
 * 这一块是「时机是这套判分的全部论点」唯一能在三分钟里被看懂的形式
 * （POSITIONING「成功标准」）。**在这里给一条时间上不可能的建议，
 * 比在别处给十条废话都贵。**
 */

/** 这一局里，`bestMood` 那一档出现在第 `round` 轮之后、之前，还是从没出现。
 *
 *  判据取 `judgedMood`——那正是判分当时认定的档位，与效力矩阵同源。
 *  第 1 轮的 `judgedMood` 就是开局档位，所以"开局那一下"也数得到。
 *
 *  @returns {'future'|'past'|'never'}
 */
function windowOf(turns, round, bestMood) {
  if (round == null) return 'never';
  if (turns.some((t) => t.round > round && t.judgedMood === bestMood)) return 'future';
  if (turns.some((t) => t.round <= round && t.judgedMood === bestMood)) return 'past';
  return 'never';
}

/** 「更好的那个时候」这半句话，**两个消费方共用一份**。
 *
 *  复盘正文与分享卡各写一套的话，同一局会给出两种说法——这个模块顶上
 *  那段说得很清楚，两边算同一件事就不能各算一套。措辞也一样。
 */
export function timingClause(f) {
  const TA = peerPronoun();
  const mood = MOODS[f.bestMood] || f.bestMood;
  // 已经过去了：不能说"等"。玩家要听的是"这句话该更早说"
  if (f.bestWindow === 'past') return `早几轮，趁${TA}还${mood}的时候说`;
  // 这一局压根没走到那一档：两头都不沾，说成无时态的条件句
  if (f.bestWindow === 'never') return `${TA}${mood}的时候说`;
  return `等${TA}${mood}再说`;
}

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
      bestWindow: windowOf(turns, mistimed.round, bestMood),
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
      bestWindow: windowOf(turns, t.round, bestMood),
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
    // 这两支不讲某一轮（`round` 是 null），措辞本来就是无时态的
    // 「在{ta}松动时值 X×」，不经过 timingClause
    bestWindow: 'never',
    worstMood,
    worstVal,
    times: ratio(bestVal, worstVal),
  };
}

/**
 * 「同一句话，换个时候说」可探索版：`contrastFacts()` 只挑一轮来讲，
 * 这个函数把**这一局里每一轮能做时机对照的证据**都摊出来，供复盘里的
 * 那个可点选小部件用（A1，2026-08-31）。
 *
 * **不改 `contrastFacts()` 一个字**：分享卡的卡面主角、复盘正文的头条
 * 都还在用它挑的那一条；这里只是从同一份 `eff` 矩阵里多榨一层数据，
 * 两者互不干扰，`sharecard.test.mjs` 那组回归测试不用动。
 *
 * 判"这一轮算不算数"的标准与 `contrastFacts()` 的分支二、一是同一条：
 * 命中了一把在效力矩阵里有行的钥匙，或者是说早了的「有据告知」。
 * 纯失误、合规红线、没命中任何东西的轮次不进这份列表——它们没有
 * "换个时候说值多少倍"可讲。
 *
 * @returns {Array<{
 *   round: number, key: string, name: string,
 *   mood: string, val: number|null, row: Record<string, number>,
 *   bestMood: string, bestVal: number, mistimed: boolean,
 * }>}
 */
export function contrastTimeline() {
  const eff = game.ending && game.ending.efficacy;
  if (!eff) return [];

  const rowOf = (k) => eff[k] || null;
  const nameOf = (k) => (KEYS[k] ? KEYS[k].name : k);

  const out = [];
  scoredTurns().forEach((t) => {
    if (t.mistimedWarning && rowOf('informed_warning')) {
      const row = rowOf('informed_warning');
      const [bestMood, bestVal] = peak(row);
      out.push({
        round: t.round, key: 'informed_warning', name: nameOf('informed_warning'),
        mood: t.judgedMood, val: null, row, bestMood, bestVal, mistimed: true,
      });
      return;
    }
    if (t.efficacy == null) return;
    const k = (t.hits || []).find((h) => rowOf(h));
    if (!k) return;
    const row = rowOf(k);
    const [bestMood, bestVal] = peak(row);
    out.push({
      round: t.round, key: k, name: nameOf(k),
      mood: t.judgedMood, val: t.efficacy, row, bestMood, bestVal, mistimed: false,
    });
  });
  return out;
}
