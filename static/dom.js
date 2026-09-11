/* 这一页里最底下那一层：拿节点、切屏、等一会儿。
 *
 * 它谁都不依赖，因此谁都可以依赖它——模块图的叶子只有这一个，
 * 别往里加任何带业务判断的东西。 */

export const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export const sleep = (ms) => new Promise((r) => setTimeout(r, REDUCED ? 0 : ms));

export const $ = (id) => document.getElementById(id);
export const thread = $('thread');

export function showScreen(id) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.toggle('on', s.id === id));
}

/** 震一下。**页面上还没发生过真实用户手势时直接不震。**
 *
 *  `navigator.vibrate()` 在没有手势的页面上会被 Chrome 拦下来，**而且拦的
 *  同时往控制台打一条 intervention 报错**——try/catch 接不住它，那不是异常，
 *  是浏览器自己打的日志。`tests/e2e/browser.test.mjs` 那条「全程一条控制台
 *  报错都没有」正是拿来守这类脏输出的，2026-09-11 被它逮到一次：首轮全屏
 *  揭晓在自动化里没有手势，每跑一次就脏一条。
 *
 *  **这道闸在真人手里一次都不会误伤**：会触发震动的三个时刻（被拦下、
 *  首轮揭晓、命中钥匙）都发生在他自己点过按钮或发过消息之后。
 *  `userActivation` 在老浏览器上是 undefined，那时候退回原来的行为。 */
export function buzz(pattern) {
  if (REDUCED) return;
  if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return;
  try { navigator.vibrate?.(pattern); } catch { /* 不支持震动就算了 */ }
}

/** 极简音效：一串正弦音，`notes` 每项 `[hz, 相对起点的偏移秒]`。
 *
 *  与 `opening.js` 的 `alarmFeedback()` 是同一套写法（同样的自动播放策略、
 *  同样的低动态偏好豁免）——那边没把这段抽出来，是因为它两声下行的调式
 *  是单独调过的、只服务"被拦下"那一刻。这里抽成公用函数，是因为它要被
 *  两个新时刻复用（第一轮揭晓、命中钥匙），没必要为每一处各写一份
 *  AudioContext 生命周期管理。
 *
 *  全程 try/catch、不 await：不支持 Web Audio 的浏览器、被自动播放策略
 *  拦下的调用（页面还没发生过真实交互），任何一个都不许挡住调用方的
 *  其余流程——这是提示音，不是必须送达的信息。 */
export function chime(notes, gain = 0.05) {
  if (REDUCED) return;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const at = ctx.currentTime;
    let maxEnd = 0;
    notes.forEach(([hz, off]) => {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = hz;
      g.gain.setValueAtTime(.0001, at + off);
      g.gain.exponentialRampToValueAtTime(gain, at + off + .012);
      g.gain.exponentialRampToValueAtTime(.0001, at + off + .16);
      osc.connect(g).connect(ctx.destination);
      osc.start(at + off);
      osc.stop(at + off + .16);
      maxEnd = Math.max(maxEnd, off + .16);
    });
    setTimeout(() => { ctx.close().catch(() => {}); }, (maxEnd + .5) * 1000);
  } catch { /* 拿不到音频就静默播出，调用方的其余部分一个字都不受影响 */ }
}
