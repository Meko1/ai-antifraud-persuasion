import { $ } from './dom.js';

// ── 底部动作面板 ──────────────────────────────────────────────────────────
//
// 退出、七种问法、换客户三处共用这一个实现。写三遍的代价不是重复代码，
// 是**三套焦点处理里必然有一套是错的**——而弹层的焦点做错，键盘用户会
// 直接被困在背景里，屏幕阅读器读的还是下面那一屏。
//
// 它管四件事，一件都不能少：
//   1. 焦点移进面板（打开时落在第一个可聚焦元素上）
//   2. Tab / Shift+Tab 在面板内循环，出不去
//   3. Esc 关闭 —— 这是"用户控制"最基本的那一下
//   4. 关闭后焦点回到打开它的那个按钮，不是回到 body

const sheetHost = $('sheetHost');
let sheetCloser = null;

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

/** 打开一个底部面板。返回一个关掉它的函数。
 *
 *  `items` 里每一项是 {label, note, onPick, tone}。tone 只影响配色，
 *  'danger' 给那些走了就回不来的动作。
 */
export function openSheet({ title, note, items, onClose }) {
  closeSheet();

  const opener = document.activeElement;
  const panel = document.createElement('div');
  panel.className = 'sheet';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  panel.setAttribute('aria-label', title);

  const head = document.createElement('div');
  head.className = 'sheet-head';
  const h = document.createElement('h2');
  h.textContent = title;
  head.appendChild(h);
  if (note) {
    const p = document.createElement('p');
    p.textContent = note;
    head.appendChild(p);
  }
  panel.appendChild(head);

  const list = document.createElement('div');
  list.className = 'sheet-list';
  items.forEach((item) => {
    // 纯展示项（七种问法那一层）不做成按钮：一个按下去什么都不发生的
    // 控件，比一段普通文字更难懂
    const node = document.createElement(item.onPick ? 'button' : 'div');
    node.className = 'sheet-item' + (item.tone ? ` ${item.tone}` : '');
    if (item.onPick) {
      node.type = 'button';
      node.onclick = () => { closeSheet(); item.onPick(); };
    }
    const b = document.createElement('b');
    b.textContent = item.label;
    node.appendChild(b);
    if (item.note) {
      const s = document.createElement('span');
      s.textContent = item.note;
      node.appendChild(s);
    }
    list.appendChild(node);
  });
  panel.appendChild(list);

  const cancel = document.createElement('button');
  cancel.className = 'sheet-cancel';
  cancel.type = 'button';
  cancel.textContent = '取消';
  cancel.onclick = () => closeSheet();
  panel.appendChild(cancel);

  sheetHost.replaceChildren(panel);
  sheetHost.hidden = false;
  sheetHost.onclick = (e) => { if (e.target === sheetHost) closeSheet(); };

  const onKey = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeSheet();
      return;
    }
    if (e.key !== 'Tab') return;
    // 焦点捕获。少了它，Tab 会走到背景那一屏的输入框上，
    // 而背景在视觉上是被盖住的——用户看不见光标去了哪儿
    const nodes = [...panel.querySelectorAll(FOCUSABLE)];
    if (!nodes.length) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  document.addEventListener('keydown', onKey, true);

  (panel.querySelector(FOCUSABLE) || panel).focus({ preventScroll: true });

  sheetCloser = () => {
    document.removeEventListener('keydown', onKey, true);
    sheetHost.hidden = true;
    sheetHost.replaceChildren();
    sheetHost.onclick = null;
    sheetCloser = null;
    // 焦点回到打开它的那个按钮。不还回去的话，键盘用户下一次 Tab
    // 会从文档开头重新走一遍
    if (opener && document.contains(opener)) opener.focus({ preventScroll: true });
    if (onClose) onClose();
  };
  return sheetCloser;
}

export function closeSheet() {
  if (sheetCloser) sheetCloser();
}
