/* 把 static/ 那一批模块（`MODULES`）装进一个沙箱里，好让它们的函数能被单测调用。
 *
 * ## 为什么是这个做法
 *
 * 前端从 2026-08-24 起拆成了一组 ES module（复核清单 P2-1，拆分前是一个
 * 3087 行的单文件），**当前清单以下面的 `MODULES` 为准**——这里不写个数，
 * 写了就得每次加模块时记得改，而实测这类数字从来没被改对过（README 同理）。
 * 浏览器那边靠 `<script type="module">` 自己解析，
 * **没有打包步骤**。这一层要在 Node 里把同一份代码跑起来。
 *
 * 三条约束决定了做法：
 *
 * 1. **不许引入 npm 依赖。** 这个仓库没有 node_modules，打包规范里也不该
 *    多出一个。所以没有 jsdom，没有 jest，也没有 rollup——只用 Node 自带的
 *    `node:vm` 与 `node --test`。
 * 2. **不许为了测试给每个用例开一个 async。** `loadApp()` 必须是同步的：
 *    真 `import()` 是异步的，改过去要动全部四十几个用例，而这一轮是**纯结构
 *    重构**，测试断言一个字都不该跟着改。
 * 3. **加载时不能真的去发请求。** `fetch` 被换成一个**永远不 resolve**
 *    的 promise：`boot()` 那条路会一直挂着，既不会打网关，也不会冒出
 *    一条 unhandled rejection 把测试染红。
 *
 * 于是这里做的事是：**按依赖顺序把这些模块拼成一份脚本，把 import /
 * export 那几行剥掉，再喂给 `node:vm`。** 拼接顺序 `MODULES` 与浏览器的
 * 求值顺序一致，所以顶层 `const` 的初始化先后关系跟真实加载是同一套。
 *
 * ## 它测得了什么，测不了什么
 *
 * 测得了：**不碰 DOM 的那些判断**——`scoredTurns` / `breachTurns` /
 * `reviewRows` / `resultAmount` 这一层。它们承载复盘里全部的"这一局你
 * 打得怎么样"，而 `degraded` 那个 bug 正是从这一层漏过去的。
 *
 * 测不了两样，都写在明处：
 *
 * · **渲染结果。** DOM 在这里是一个"问什么都答应"的替身，`innerHTML`
 *   赋值不会真的解析成节点。复盘那条"第一屏五块"的硬上限因此走另一条路：
 *   直接读源码里的模板（见 review.test.mjs 末尾那一组）。
 * · **模块之间的连线。** 拼接之后所有名字落在同一个作用域里，
 *   漏写一句 `import` 在这儿照样跑得通，到浏览器里才炸。这个洞由
 *   modules.test.mjs（静态查 import/export 对不对得上）与
 *   tests/e2e/browser.test.mjs（真浏览器加载真模块图）两头堵。
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const STATIC = path.join(HERE, '..', '..', 'static');

/** 拼接顺序 = 浏览器的模块求值顺序：被依赖的排在前面。
 *
 *  **改动这个数组前先想清楚。** 顺序错了，顶层 `const` 会在初始化之前
 *  被读到——那正是 `RESUME_KEY` 当初出事的方式（见 resume.test.mjs 里
 *  「声明顺序」那一组，它现在钉的就是这份拼接结果）。 */
export const MODULES = [
  'dom.js', 'keys.js', 'state.js', 'api.js', 'stats.js', 'chart.js', 'qr.js',
  'contrast.js', 'sheet.js', 'chat.js', 'review.js', 'history.js', 'opening.js',
  'control.js', 'app.js',
];

/** 入口模块。留着这个名字是因为它仍然是浏览器唯一加载的那个文件。 */
export const APP_JS = path.join(STATIC, 'app.js');

/** 某一个模块的源码。断言"这个函数体里不许出现 X"时用它，别用 SOURCE——
 *  在整份拼接结果上做子串判断，很容易误伤到隔壁模块。 */
export function sourceOf(name) {
  if (!MODULES.includes(name)) throw new Error(`不是前端模块：${name}`);
  return fs.readFileSync(path.join(STATIC, name), 'utf8');
}

/** 全部模块拼起来的那一份，顺序同 MODULES。
 *
 *  只有**跨模块**的断言该用它（比如"全前端不许再有第二处 location.reload"）。 */
export const SOURCE = MODULES.map(sourceOf).join('\n');

/** 从源码里切出一个顶层函数的完整定义（含函数体）。
 *
 *  在此之前这几处断言是 `src.slice(indexOf('function A'), indexOf('function B'))`
 *  ——**切的是"A 到 B 之间"，不是"A 的函数体"**。拆分之后 A 与 B 落进了
 *  不同的文件，那种切法要么切空、要么把中间隔的几百行一起切进来，
 *  于是断言"这里面不许出现 await fetch"会在隔壁模块上误红。
 *
 *  数花括号，只认成对的那个收尾。够用了：本仓库的顶层函数里没有
 *  含花括号的字符串或正则字面量（modules.test.mjs 顺手钉了这一条）。 */
export function bodyOf(source, name) {
  const head = new RegExp(`^(?:export\\s+)?(?:async\\s+)?function\\s+${name}\\s*\\(`, 'm');
  const at = source.search(head);
  if (at < 0) throw new Error(`源码里找不到顶层函数 ${name}`);
  const open = source.indexOf('{', at);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(at, i + 1);
    }
  }
  throw new Error(`${name} 的花括号没配平`);
}

/* import / export 那几行在拼接之后既没用也跑不了（vm.Script 不是模块）。
 *
 * 剥掉而不是留着：`import` 在非模块脚本里是语法错误，整份直接加载不了。
 * 只处理**行首**的那几种形式——本仓库的模块全是这么写的，
 * modules.test.mjs 会把这条约定钉住，免得哪天冒出一句剥不掉的写法。 */
const IMPORT_LINE = /^import[\s{][^;]*?;\s*$/gms;
const EXPORT_KEYWORD = /^export\s+(?=(?:default\s+)?(?:const|let|var|function|async|class)\b)/gm;

function flatten(source) {
  return source.replace(IMPORT_LINE, '').replace(EXPORT_KEYWORD, '');
}

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


/** 一份最小的 Storage 替身。localStorage 与 sessionStorage 各要一份**独立的**，
 *  共用一个对象会让"续局存在 session 里、primer 存在 local 里"这条分工失效。 */
function store() {
  return {
    _v: {},
    getItem(k) { return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; },
    setItem(k, v) { this._v[k] = String(v); },
    removeItem(k) { delete this._v[k]; },
    clear() { this._v = {}; },
  };
}

/** 加载整个模块图，返回它的顶层作用域。
 *
 *  `vm.createContext` 之后，脚本里所有的 `function` 都挂在这个 context
 *  对象上，于是 `sandbox.scoredTurns` 就是那个函数本身。
 */
export function loadApp() {
  const dom = anything();
  const sandbox = {
    document: dom,
    window: dom,
    navigator: { userAgent: 'node' },
    location: { href: 'http://localhost/', reload() { this._reloaded = true; } },
    console,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    // 永远挂着：加载期那个 ready IIFE 因此既不发请求，也不报错
    fetch: () => new Promise(() => {}),
    localStorage: store(),
    // 续局存在这里（app.js 的 `aap.game.v1`）。**与 localStorage 分开**
    // 不是随手写的：sessionStorage 标签页一关就没，正是"这一次干预"该有的寿命
    sessionStorage: store(),
    Date,
    Math,
    JSON,
    URL,
  };
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;

  vm.createContext(sandbox);
  new vm.Script(flatten(SOURCE) + EPILOGUE, { filename: 'static/<modules>' })
    .runInContext(sandbox);
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
 * **前端源码本身一个字都没为测试改过。** 这一段只在测试加载时拼在后面。
 *
 * `set SCENE` 在这里仍然是直接赋值：拼接之后一切都在同一个脚本作用域里，
 * 没有 ES module 那条"导入绑定只读"的限制。浏览器那边走 `setScene()`。 */
const EPILOGUE = `
;globalThis.__app = {
  get game() { return game; },
  get KEYS() { return KEYS; },
  get PENALTIES() { return PENALTIES; },
  get BREACHES() { return BREACHES; },
  get TONES() { return TONES; },
  get MOODS() { return MOODS; },
  get RESULT_BASIS() { return RESULT_BASIS; },
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
