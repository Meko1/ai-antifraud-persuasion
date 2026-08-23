/* 把 static/app.js 装进一个沙箱里，好让它的函数能被单测调用。
 *
 * ## 为什么是这个做法
 *
 * `static/app.js` 是一个 2400 多行的单文件脚本：没有 export，全部逻辑挂在
 * 顶层作用域上，加载时还会直接去摸 DOM 和发请求。要测它，先得能加载它。
 *
 * 三条约束决定了做法：
 *
 * 1. **不许引入 npm 依赖。** 这个仓库没有 node_modules，打包规范里也不该
 *    多出一个。所以没有 jsdom，没有 jest——只用 Node 自带的 `node:vm`
 *    与 `node --test`。
 * 2. **不许为了测试去改 app.js 的结构。** 加 `export` 就得配打包器，
 *    而浏览器那边现在是一个 `<script src>` 直接跑，改一次就多一层。
 * 3. **加载时不能真的去发请求。** `fetch` 被换成一个**永远不 resolve**
 *    的 promise：脚本末尾那个 `ready` IIFE 会一直挂着，既不会打网关，
 *    也不会冒出一条 unhandled rejection 把测试染红。
 *
 * ## 它测得了什么，测不了什么
 *
 * 测得了：**不碰 DOM 的那些判断**——`scoredTurns` / `breachTurns` /
 * `reviewRows` / `resultAmount` 这一层。它们承载复盘里全部的"这一局你
 * 打得怎么样"，而 `degraded` 那个 bug 正是从这一层漏过去的。
 *
 * 测不了：渲染结果。DOM 在这里是一个"问什么都答应"的替身，
 * `innerHTML` 赋值不会真的解析成节点。要断言渲染，就得有 jsdom 或一个
 * 真浏览器，那是第 1 条不让做的事。复盘那条"第一屏五块"的硬上限因此
 * 走另一条路：直接读源码里的模板（见 review.test.mjs 末尾那一组）。
 * **这一点写在明处，免得后来的人以为这套测试盖住了渲染。**
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const APP_JS = path.join(HERE, '..', '..', 'static', 'app.js');

/** 一个"问什么都答应"的替身。属性访问、调用、下标全都返回它自己。
 *
 *  它不是在模拟 DOM，是在**不挡路**：app.js 顶层要摸十几个节点、
 *  挂五六个监听器，任何一处抛异常，整个文件就加载不了。
 */
function anything() {
  const target = function () {};
  const proxy = new Proxy(target, {
    get(_t, prop) {
      // then 必须是 undefined：否则 await 一个替身会把它当 thenable，
      // 拿着自己当 resolve 回调调用，直接卡死
      if (prop === 'then') return undefined;
      if (prop === Symbol.toPrimitive) return () => '';
      if (prop === Symbol.iterator) return function* () {};
      if (prop === 'length') return 0;
      if (prop === 'value' || prop === 'textContent' || prop === 'innerHTML') return '';
      return proxy;
    },
    set: () => true,
    has: () => true,
    apply: () => proxy,
    construct: () => proxy,
  });
  return proxy;
}

/** 加载 app.js，返回它的顶层作用域。
 *
 *  `vm.createContext` 之后，脚本里所有的 `const` / `function` 都挂在
 *  这个 context 对象上，于是 `sandbox.scoredTurns` 就是那个函数本身。
 */
export function loadApp() {
  const dom = anything();
  const sandbox = {
    document: dom,
    window: dom,
    navigator: { userAgent: 'node' },
    location: { href: 'http://localhost/' },
    console,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    // 永远挂着：加载期那个 ready IIFE 因此既不发请求，也不报错
    fetch: () => new Promise(() => {}),
    localStorage: {
      _v: {},
      getItem(k) { return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; },
      setItem(k, v) { this._v[k] = String(v); },
      removeItem(k) { delete this._v[k]; },
    },
    Date,
    Math,
    JSON,
    URL,
  };
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;

  vm.createContext(sandbox);
  const source = fs.readFileSync(APP_JS, 'utf8');
  new vm.Script(source + EPILOGUE, { filename: 'static/app.js' }).runInContext(sandbox);
  // 函数声明会自己挂到 context 上，`const` / `let` 不会——所以状态那几个
  // 走 EPILOGUE 交出来的访问器，函数直接从 sandbox 上取
  return Object.assign(Object.create(sandbox.__app), sandbox);
}

/* 顶层的 `const game` / `let SCENE` 不会出现在 context 上（这是 ESM 之前
 * 就有的老规矩：词法声明进的是脚本作用域，不是全局对象）。
 *
 * 于是在源码**后面**追一段访问器把它们交出来。注意是 getter 不是快照：
 * `SCENE` 是 `let`，开局那一下才被赋值，抄一份快照拿到的永远是 null。
 *
 * **app.js 本身一个字都没改。** 这一段只在测试加载时拼在后面。 */
const EPILOGUE = `
;globalThis.__app = {
  get game() { return game; },
  get KEYS() { return KEYS; },
  get PENALTIES() { return PENALTIES; },
  get BREACHES() { return BREACHES; },
  get TONES() { return TONES; },
  get MOODS() { return MOODS; },
  get SCENE() { return SCENE; },
  set SCENE(v) { SCENE = v; },
};
`;

/** 造一轮对局记录。只写这一条测试关心的字段，其余给个说得通的默认值。 */
export function turn(overrides = {}) {
  return {
    round: 1,
    utterance: '这笔钱本来是打算做什么用的？',
    reply: '你管我',
    lines: ['你管我'],
    hits: [],
    grounded: true,
    delta: 0,
    trust: 30,
    before: 30,
    pool: 0,
    efficacy: 1.0,
    judgedMood: 'guarded',
    degraded: false,
    pressure: false,
    ...overrides,
  };
}
