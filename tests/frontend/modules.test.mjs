/* 模块之间的连线对不对得上。
 *
 * ## 它堵的是哪个洞
 *
 * 单测那一层（harness.mjs）把十三个模块**拼成一份脚本**再跑，于是所有名字
 * 落在同一个作用域里——**漏写一句 `import` 在那儿照样跑得通，到浏览器里
 * 才炸**。这正是"拼接"这个取巧做法的代价，写在 harness.mjs 顶部。
 *
 * 这一组静态地把那个洞堵上：每一句 `import { x } from './y.js'` 里的 x，
 * y.js 都得真的 export 了；每一个用到的名字都得有来路。
 *
 * 另一半由 tests/e2e/browser.test.mjs 堵——那边是真浏览器加载真模块图。
 * 两者的分工：这一组**总是能跑**（只读文件），E2E 要有 Chrome。
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test, describe } from 'node:test';

import { MODULES, SOURCE, STATIC, sourceOf } from './harness.mjs';

/** 一个模块 export 出来的名字。只认行首那几种写法——modules 里全是这么写的，
 *  下面「写法是约定好的那几种」那一条把这个前提钉住。 */
function exportsOf(src) {
  const names = new Set();
  const re = /^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)/gm;
  for (const m of src.matchAll(re)) names.add(m[1]);
  return names;
}

/** 一个模块 import 进来的东西：[{from, names}]。 */
function importsOf(src) {
  const out = [];
  const re = /^import\s*\{([^}]*)\}\s*from\s*'(\.\/[\w.]+)';/gms;
  for (const m of src.matchAll(re)) {
    out.push({
      from: m[2].replace('./', ''),
      names: m[1].split(',').map((s) => s.trim()).filter(Boolean),
    });
  }
  return out;
}

/** 把注释与字符串的**内容**抹成空格，长度与位置不变。
 *
 *  下面那组"用到的名字都得导入"要在源码里找标识符引用，而这个仓库的注释是
 *  中英夹杂的长段落、字符串里全是 `'avatar '` `$('say')` 这种和函数同名的
 *  片段——不抹掉的话它满屏误报，两天之内就没人再看它了。
 *
 *  模板串里的 `${...}` **保留**：那里面是真代码，`${peerPronoun()}` 这种
 *  跨模块调用只出现在插值里，抹掉就等于把要查的东西查漏了。
 *
 *  手写扫描器，不上 parser——那要么是 npm 依赖，要么是另一个更大的坑。 */
function blankOut(src) {
  const out = src.split('');
  const blank = (from, to) => {
    for (let i = from; i < to; i += 1) if (out[i] !== '\n') out[i] = ' ';
  };
  let i = 0;
  const n = src.length;
  const tpl = [];  // 模板串的嵌套栈：进到 ${} 里就暂停抹字
  while (i < n) {
    const c = src[i];
    if (tpl.length === 0 || tpl[tpl.length - 1] === 'code') {
      if (c === '/' && src[i + 1] === '/') {
        const end = src.indexOf('\n', i);
        blank(i + 2, end < 0 ? n : end);
        i = end < 0 ? n : end;
        continue;
      }
      if (c === '/' && src[i + 1] === '*') {
        const end = src.indexOf('*/', i + 2);
        blank(i + 2, end < 0 ? n : end);
        i = end < 0 ? n : end + 2;
        continue;
      }
    }
    if (c === "'" || c === '"') {
      let j = i + 1;
      while (j < n && src[j] !== c) j += src[j] === '\\' ? 2 : 1;
      blank(i + 1, j);
      i = j + 1;
      continue;
    }
    if (c === '`') {
      tpl.push('tpl');
      i += 1;
      // 一路抹到 ${ 或收尾的反引号
      while (i < n) {
        if (src[i] === '\\') { out[i] = ' '; out[i + 1] = ' '; i += 2; continue; }
        if (src[i] === '`') { tpl.pop(); i += 1; break; }
        if (src[i] === '$' && src[i + 1] === '{') { tpl.push('code'); i += 2; break; }
        if (src[i] !== '\n') out[i] = ' ';
        i += 1;
      }
      continue;
    }
    if (c === '}' && tpl[tpl.length - 1] === 'code') {
      tpl.pop();          // 出了 ${}，回到模板串里继续抹
      i += 1;
      while (i < n) {
        if (src[i] === '\\') { out[i] = ' '; out[i + 1] = ' '; i += 2; continue; }
        if (src[i] === '`') { tpl.pop(); i += 1; break; }
        if (src[i] === '$' && src[i + 1] === '{') { tpl.push('code'); i += 2; break; }
        if (src[i] !== '\n') out[i] = ' ';
        i += 1;
      }
      continue;
    }
    i += 1;
  }
  return out.join('');
}

const SRC = Object.fromEntries(MODULES.map((n) => [n, sourceOf(n)]));

describe('模块清单与目录对得上', () => {
  test('static/ 下的 .js 一个不多一个不少', () => {
    const onDisk = fs.readdirSync(STATIC).filter((f) => f.endsWith('.js')).sort();
    assert.deepEqual(onDisk, [...MODULES].sort(),
      'static/ 下的 .js 与 harness.mjs 的 MODULES 对不上——'
      + '新加的模块没进拼接顺序的话，单测根本看不见它');
  });

  test('入口是 app.js，而且它是最后一个求值的', () => {
    assert.equal(MODULES[MODULES.length - 1], 'app.js',
      '入口必须排在最后：点火那一下要等所有模块体求值完（见 opening.js 的 boot）');
  });

  test('index.html 用 type="module" 加载入口', () => {
    const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
    assert.match(html, /<script\s+type="module"\s+src="static\/app\.js"><\/script>/,
      '少了 type="module" 就是一句 SyntaxError——没有打包步骤兜着');
  });
});

describe('每一句 import 都有来路', () => {
  for (const name of MODULES) {
    test(`${name}`, () => {
      for (const { from, names } of importsOf(SRC[name])) {
        assert.ok(MODULES.includes(from),
          `${name} 从 ${from} 导入，但那不是一个前端模块`);
        const 有的 = exportsOf(SRC[from]);
        for (const n of names) {
          assert.ok(有的.has(n),
            `${name} 想要 ${from} 的 ${n}，而 ${from} 没有 export 它。`
            + '（拼接跑的单测看不出这个，浏览器一加载就白屏）');
        }
      }
    });
  }
});

/** 这个名字在源码里被**当成值用过**没有。
 *
 *  对象字面量的键不算：`{ receipt: null }` 里的 `receipt` 跟 chat.js 那个
 *  同名函数毫无关系，而这两张表（endingMeta 的返回值、RECEIPT）里全是这种键。
 *  判据是"前面是 `{` / `,` / 行首，后面紧跟 `:`"——三元里的 `a ? receipt : b`
 *  前面是 `? `，因此仍然算一次真引用。 */
function 找得到引用(body, re, len) {
  for (const m of body.matchAll(re)) {
    const 后 = body.slice(m.index + len, m.index + len + 8);
    const 前 = body.slice(Math.max(0, m.index - 40), m.index);
    if (/^\s*:/.test(后) && /(^|[{,\n])\s*$/.test(前)) continue;
    return true;
  }
  return false;
}

describe('用到的名字都得是自己的或导入的', () => {
  /* 反过来查一遍：一个模块里出现的跨模块函数名，必须要么是它自己定义的，
     要么在它的 import 列表里。**这一条抓的正是"忘了写 import"**——
     拼接之后那个名字仍然找得到，所以单测全绿，浏览器里是 ReferenceError。 */
  const 自己定义的 = (src) => {
    const names = new Set();
    const re = /^(?:export\s+)?(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)/gm;
    for (const m of src.matchAll(re)) names.add(m[1]);
    return names;
  };

  // 全前端的顶层名字。只查这些——局部变量、DOM API、内建对象一概不管
  const 全部导出 = new Map();
  for (const name of MODULES) {
    for (const n of exportsOf(SRC[name])) 全部导出.set(n, name);
  }

  for (const name of MODULES) {
    test(`${name}`, () => {
      const src = SRC[name];
      const 本地 = 自己定义的(src);
      const 导入 = new Set(importsOf(src).flatMap((i) => i.names));
      // 把 import 那几行剥掉再找引用，否则 import 语句自己会被当成使用
      const body = blankOut(src).replace(/^import[\s{][^;]*?;\s*$/gms, '');
      for (const [n, owner] of 全部导出) {
        if (owner === name || 本地.has(n) || 导入.has(n)) continue;
        // `$` 在模板串里到处都是（`${...}`），所以它只认"后面跟着左括号"那一种
        const esc = n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const re = n === '$'
          ? /(?<![\w$.])\$(?=\()/g
          : new RegExp(`(?<![\\w$.])${esc}(?![\\w$])`, 'g');
        assert.ok(!找得到引用(body, re, n.length),
          `${name} 用了 ${owner} 的 ${n}，但没有 import 它`);
      }
    });
  }
});

describe('没有白导入的名字', () => {
  /* import 了却没用上的名字。**它是搬代码留下的残渣**：函数挪去别的模块了，
     import 那一行忘了跟着删。拆分那一轮四个模块上各留了一个（`peerPronoun`
     留在 chat.js、`$`/`thread` 留在 review.js、`syncSend` 留在 opening.js），
     全是这么来的。

     它不会让程序出错，但会让"谁依赖谁"这张图说谎——而那张图正是这次拆分
     唯一的产出。 */
  for (const name of MODULES) {
    test(`${name}`, () => {
      const body = blankOut(SRC[name]).replace(/^import[\s{][^;]*?;\s*$/gms, '');
      const 没用上 = importsOf(SRC[name]).flatMap((i) => i.names).filter((n) => {
        const esc = n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const re = n === '$'
          ? /(?<![\w$.])\$(?=\()/g
          : new RegExp(`(?<![\\w$.])${esc}(?![\\w$])`, 'g');
        return !找得到引用(body, re, n.length);
      });
      assert.deepEqual(没用上, [],
        `${name} import 了但没用上：${没用上.join('、')}`);
    });
  }
});

describe('写法是约定好的那几种（harness 的拼接靠它）', () => {
  test('import 都在行首、都是具名花括号形式', () => {
    for (const name of MODULES) {
      for (const line of SRC[name].split('\n')) {
        if (!/^\s*import\b/.test(line)) continue;
        assert.match(line, /^import\s*\{/,
          `${name} 里有一句 harness 剥不掉的 import：${line.trim()}`
          + '（默认导入 / 命名空间导入 / 副作用导入都没在支持之列）');
      }
    }
  });

  test('拼接之后不许再剩下任何模块语法', () => {
    // 剩一句 `import`，vm.Script 会当场 SyntaxError，整套单测一条都跑不了
    const flat = SOURCE
      .replace(/^import[\s{][^;]*?;\s*$/gms, '')
      .replace(/^export\s+(?=(?:default\s+)?(?:const|let|var|function|async|class)\b)/gm, '');
    const 残留 = flat.split('\n')
      .filter((l) => /^\s*(import|export)\b/.test(l));
    assert.deepEqual(残留, [], `拼接后还剩模块语法：\n${残留.join('\n')}`);
  });

  test('没有 export default', () => {
    // 十三个模块全是具名导出。default 会让上面那套静态检查失明
    for (const name of MODULES) {
      assert.doesNotMatch(SRC[name], /^export\s+default\b/m, `${name} 用了 export default`);
    }
  });
});

describe('方向只有一条：视图依赖状态，状态不依赖视图', () => {
  const 底座 = ['dom.js', 'keys.js', 'state.js', 'api.js', 'stats.js', 'chart.js'];
  const 视图 = ['sheet.js', 'chat.js', 'review.js', 'history.js', 'opening.js', 'control.js'];

  for (const name of 底座) {
    test(`${name} 不认识任何视图模块`, () => {
      for (const { from } of importsOf(SRC[name])) {
        assert.ok(!视图.includes(from) && from !== 'app.js',
          `${name} 依赖了视图模块 ${from} —— 这条线一旦反过来，`
          + '状态就再也不能脱离 DOM 单测了，而复盘全部的判断都在那一层');
      }
    });
  }
});
