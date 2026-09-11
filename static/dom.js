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
