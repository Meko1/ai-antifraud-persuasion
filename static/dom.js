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
