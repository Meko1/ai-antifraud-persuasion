/* 界面文案里不许写死「他」。跑法：`node --test tests/frontend/`
 *
 * ## 这份文件为什么存在
 *
 * `keys.js` 末尾 2026-08-29 就写下了这条规矩：
 *
 * > **以后再往界面加指代客户的文案，一律走 `{ta}` 或 `peerPronoun()`。**
 *
 * 然后**它只落地了一处**（`MOOD_HINTS`）。第二天实测周淑琴那一局
 * （`pronoun='她'`）：
 *
 * · 开打前那一屏七把钥匙七个「他」，正下方输入框写着「输入你想对**她**说的话」；
 * · 对局中的钥匙抽屉同一份文案，而它上方的档位提示写的是「**她**开始犯嘀咕了」；
 * · 复盘展开后**整屏 18 个「他」对 4 个「她」**，逐轮那张表逐行交替
 *   （「真正改变**她**的是第 3 轮」紧挨着「**他**当时烦躁 · 这一招值 0.6×」）；
 * · 分享卡——唯一会出现在别人手机上的东西——两处写死。
 *
 * **一条没有测试的规矩不是规矩，是一句愿望。** 这份文件把它变成门禁：
 * 界面文案里出现裸的「他」当场红，而且指得出是哪一行。
 *
 * ## 判据
 *
 * 扫的是**注释之外的字符串字面量**（JS）与**注释之外的正文**（HTML）。
 * 注释里随便写——那几百行注释正是这个仓库的说明书，改它们没有意义。
 *
 * 放行三种，都不是"指代本局客户"：
 *  · `他们`——泛指受害者（"拦下他们的那句话"），不是某一个人；
 *  · `peerPronoun()` 自己那个默认值（`SCENE` 还没到时的退路）；
 *  · `state.js` 的 `replayed`：那句里的"他"指服务端，已改成不用代词的说法，
 *    这里连带钉住"别再有人把它改回代词"。
 */

import assert from 'node:assert/strict';
import test, { describe } from 'node:test';

import fs from 'node:fs';
import path from 'node:path';

import { MODULES, STATIC, bodyOf, sourceOf } from './harness.mjs';

/** 把注释挖空，保留换行——行号才对得上，红的时候人才找得到。
 *
 *  自己数引号而不是用正则：`'…//…'` 这种字符串里的斜杠会被正则误当注释开头，
 *  于是后半个文件整片被挖掉，测试从此对什么都放行。 */
function stripJsComments(src) {
  let out = '';
  let mode = 'code';  // code | line | block | sq | dq | tpl
  for (let i = 0; i < src.length; i += 1) {
    const c = src[i];
    const d = src[i + 1];
    if (mode === 'code') {
      if (c === '/' && d === '/') { mode = 'line'; out += '  '; i += 1; continue; }
      if (c === '/' && d === '*') { mode = 'block'; out += '  '; i += 1; continue; }
      if (c === "'") mode = 'sq';
      else if (c === '"') mode = 'dq';
      else if (c === '`') mode = 'tpl';
      out += c;
      continue;
    }
    if (mode === 'line') {
      if (c === '\n') { mode = 'code'; out += '\n'; } else out += ' ';
      continue;
    }
    if (mode === 'block') {
      if (c === '*' && d === '/') { mode = 'code'; out += '  '; i += 1; continue; }
      out += (c === '\n' ? '\n' : ' ');
      continue;
    }
    // 字符串内部：转义序列整个跳过，免得 \' 把结尾认错
    if (c === '\\') { out += c + (d ?? ''); i += 1; continue; }
    if ((mode === 'sq' && c === "'")
      || (mode === 'dq' && c === '"')
      || (mode === 'tpl' && c === '`')) mode = 'code';
    out += c;
  }
  return out;
}

/** 裸的「他」——放掉泛指的「他们」。 */
const BARE = /他(?!们)/;

/** 把 `<!-- -->` 挖掉。
 *
 *  界面 HTML 是拼在模板字符串里的，**注释也拼在里面**——`review.js` 那张
 *  复盘模板里有大段 `<!-- -->` 在讲每一块为什么存在，里面自然会提到"他"。
 *  那些是给下一个改代码的人看的，不是给玩家看的。 */
const stripHtmlComments = (s) => s.replace(/<!--[\s\S]*?-->/g, ' ');

/** 一个模块里所有"注释之外的字符串字面量"，带行号。
 *
 *  **模板字符串按整段取，不按行取。** 第一版是逐行跑 `` /`[^`]*`/ ``，
 *  于是 `review.js` 那张几十行的复盘模板一处都没被扫到——里面那四条
 *  投顾处置清单写死着「他」，测试却是绿的。跨行的字面量正是这个仓库
 *  拼 HTML 的主要方式，漏掉它等于漏掉一半的界面文案。 */
function literalsOf(name) {
  const src = stripJsComments(sourceOf(name));
  const out = [];
  const re = /`[^`\\]*(?:\\[\s\S][^`\\]*)*`|'[^'\\\n]*(?:\\.[^'\\\n]*)*'|"[^"\\\n]*(?:\\.[^"\\\n]*)*"/g;
  let m = re.exec(src);
  while (m) {
    // 行号 = 这段字面量**起点**之前有多少个换行
    const line = src.slice(0, m.index).split('\n').length;
    out.push({ line, text: m[0] });
    m = re.exec(src);
  }
  return out;
}

/** `peerPronoun` 的默认值那一行。它**必须**留着裸的「他」——
 *  `SCENE` 还没到时总得回一个字，而这份测试不该逼着它回一个空串。 */
const 默认值 = /peerPronoun = \(\) =>/;

describe('代词：界面文案一律走 {ta}，不许写死「他」', () => {
  for (const name of MODULES) {
    test(`${name}`, () => {
      const 行 = stripJsComments(sourceOf(name)).split('\n');
      const 犯规 = literalsOf(name).filter(({ line, text }) => {
        if (!BARE.test(stripHtmlComments(text))) return false;
        return !默认值.test(行[line - 1]);
      });
      assert.deepEqual(犯规, [],
        `写死了代词。改成 {ta} 并让渲染方过一遍 withTa()：\n`
        + 犯规.map((h) => `    static/${name}:${h.line}  ${h.text}`).join('\n'));
    });
  }

  test('index.html 的正文同样不许写死', () => {
    // 拦截屏那一行与开打前那一屏的落款都栽在这儿过：两处都在客户名字
    // 前后两屏之内，写死「他」六成的对局当场穿帮
    const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8')
      .replace(/<!--[\s\S]*?-->/g, (s) => s.replace(/[^\n]/g, ' '));
    const 犯规 = html.split('\n')
      .map((text, n) => ({ line: n + 1, text: text.trim() }))
      .filter(({ text }) => BARE.test(text));
    assert.deepEqual(犯规, [],
      '写死了代词，改成 {ta} 并在对应的 paint 里换掉：\n'
      + 犯规.map((h) => `    static/index.html:${h.line}  ${h.text}`).join('\n'));
  });

  test('换代词只有一个出口，别再各写各的 replaceAll', () => {
    /* 出口只有一个，这份测试才钉得住整片——否则它只能维护一张
     * 永远漏项的白名单。`state.js` 是 `withTa` 的定义处，放行。 */
    const 私自替换 = MODULES
      .filter((m) => m !== 'state.js')
      .filter((m) => /replaceAll\(\s*['"`]\{ta\}/.test(stripJsComments(sourceOf(m))));
    assert.deepEqual(私自替换, [], '这些模块绕过了 withTa()');
  });
});

/* ── 反方向那一半（2026-08-31 补）────────────────────────────────────────
 *
 * **上面整组测试是单向的**，这是它 8-30 立起来之后仍然漏掉一个 bug 的原因。
 *
 * 它问的是"源码里有没有写死「他」"。而 `history.js` 那处犯的是另一个错：
 * 代词没写死，`{ta}` 也写对了，**只是取出来之后忘了过 withTa**——
 *
 *     weakBox.querySelector('.keynote').textContent = KEYS[weakest].tip;
 *
 * 于是复盘「详细复盘 → 关键方法」那张卡上，屏幕原样印着：
 *
 *     被洗脑的人满脑子是收益率。让{ta}自己说出「这笔钱本来是给孩子办婚礼的」…
 *
 * 实测可见，而 158 条前端测试全绿——因为没有一条问过"`{ta}` 有没有被换掉"。
 * 写死代词是印错人，占位符裸奔是**印出一段代码**，后者更难看。
 *
 * 下面两条把另一半补上：字面量那一侧、以及从词表里取文案那一侧。 */
describe('代词：{ta} 必须被换掉，不许裸着进 DOM', () => {
  /* **没有"每个 {ta} 字面量都得紧挨着 withTa("那一条**，试过，删了。
   *
   * 判据做不准：`withTa('…' + '…{ta}…' + '…')` 这种多行拼接里，带占位符的
   * 是中间那一段，它前面挨着的是一个 `+`；`WINDOW_NOTE` 那张表的值也带
   * `{ta}`，而换它的是几十行之外的 `withTa(w.text)`。第一版当场把
   * review.js 五处、opening.js 一处包得好好的代码报成犯规。
   *
   * **一条会误报的门禁，最后一定会被人加白名单绕过去，然后就再也拦不住东西了。**
   * 所以这一侧只留下面那条判据明确的，运行时那一半交给真浏览器：
   * `tests/e2e/browser.test.mjs` 的「整页没有一个 {ta} 裸奔到屏幕上」——
   * 它扫的是渲染完的文本节点，零误报，而且管的正是我们真正在乎的那件事。 */

  /* 开打前那一屏：**按时序钉，不按源码钉。**
   *
   * `enterGame()` 显示这一屏时**不等开局请求**（那是有意的：读这一屏的时间
   * 在给「首屏 ≤3 秒」买单）。于是慢网下 `paintPrimer()` 跑的时候 `SCENE`
   * 还是 null，`peerPronoun()` 退回默认「他」。
   *
   * 原先脚注按 `dataset.ta` 判、七条列表按 `childElementCount` 判——
   * **两道闸判据不一样**：开局响应到了，脚注重刷成「她」，列表岿然不动。
   * 实测周淑琴那一局：同一屏 6 行「他」配 1 行「她」，而下一屏的输入框
   * 写着「输入你想对她说的话」。
   *
   * 上面那几条静态扫描一个字都看不见这个 bug——**源码是对的，错的是时机**。
   * 五个场景里三位是「她」，且这一屏只对首次玩家显示（大赛点进来的那一批）。 */
  /* 沙箱里读不到渲染结果（DOM 是个替身，见 harness.mjs 顶部），所以这两条
     钉的是**判据本身**——跟 review.test.mjs 那几条读模板的做法同一路数。 */
  test('开打前那一屏的闸按代词判，不按"画过没有"判', () => {
    const body = bodyOf(sourceOf('opening.js'), 'paintPrimer');
    assert.doesNotMatch(body, /childElementCount/,
      '七条列表又按"画过没有"提前返回了 —— 慢网下代词就永远停在默认值');
    assert.match(body, /list\.dataset\.ta/,
      '列表那道闸得和脚注同一个判据（dataset.ta），否则两半会各走各的');
  });

  test('开局响应到了要再画一次首屏，否则那道闸没人触发', () => {
    // `enterGame()` 显示这一屏时不等开局请求（有意的），所以重刷只能由
    // 拿到 scenario 的那一处发起。少这一行，上面那道闸形同虚设。
    const src = stripJsComments(sourceOf('opening.js'));
    const 起始处 = src.indexOf('setScene(data.scenario');
    assert.ok(起始处 > 0, 'setScene(data.scenario …) 不在了，这条测试得跟着改');
    assert.match(src.slice(起始处, 起始处 + 400), /paintPrimer\(\)/,
      '拿到本局客户之后没有重画首屏');
  });

  /** 词表里那几段文案**每一条都带 `{ta}`**，取出来就得当场换掉。
   *  这条才是接住 `history.js` 那个 bug 的那一条——它不是字面量。 */
  test('从词表里取的文案都过了 withTa()', () => {
    /* **锚点落在字段本身，不落在表名。** 第一版写的是
     * `/(?:KEYS|…)[^;\n]{0,60}?\.(?:tip|brief)/`，于是
     * `KEYS[k] ? withTa(KEYS[k].brief)` 这种三元写法从**三元的条件**那一处
     * 起匹，往回看的窗口停在 `withTa(` 前面，把一处包好的代码报成犯规。
     * 从 `.tip` / `.brief` 自己起匹，窗口才一定罩得住那个 `withTa(`。
     *
     * 全仓的 `.tip` / `.brief` 都来自 keys.js 那三张表（grep 可查），
     * 所以不必再限定表名——限定了反而会漏掉 `meta.tip` 这类取了别名的写法。 */
    const 取文案 = /\.(?:tip|brief)\b|\bMOOD_HINTS\s*\[/g;
    const 犯规 = [];
    for (const name of MODULES) {
      if (name === 'keys.js') continue;
      const src = stripJsComments(sourceOf(name));
      let m = 取文案.exec(src);
      while (m) {
        // 往回看 80 字符，要求有一个**还没闭合**的 withTa(。
        // `[^()]*$` 那一段是关键：中间要是出现过 `)`，说明那个 withTa
        // 早就闭合了，管不到这一处（`querySelector(…).textContent = KEYS[x].tip`
        // 正是这么漏过去的）。
        const 前文 = src.slice(Math.max(0, m.index - 80), m.index);
        if (!/withTa\([^()]*$/.test(前文)) {
          const line = src.slice(0, m.index).split('\n').length;
          犯规.push(`    static/${name}:${line}  ${m[0]}`);
        }
        m = 取文案.exec(src);
      }
    }
    assert.deepEqual(犯规, [],
      `词表文案里带 {ta}，取出来必须过 withTa()：\n${犯规.join('\n')}`);
  });
});

describe('复盘不许对玩家断言剧本里没发生过的事', () => {
  /* 2026-08-30 修。`paintContrast` 的 mistimed 那一支原先写死着
   * 「跟他女儿昨天说的那四个字」——照老陈写的，而这一支在四个场景上都可达：
   * 刘卫东的女儿是**三天前**说的、林月娥**没有女儿**（是老公）、
   * 顾之然的手机里**根本没有家人**。一半的可达场景里，复盘凭空造了一个人。 */

  test('那半句取自服务端下发的 warned_by，不写死任何家人', () => {
    const src = stripJsComments(sourceOf('review.js'));
    assert.doesNotMatch(src, /女儿/, '复盘不许写死任何一个场景的家人');
    assert.match(src, /SCENE\.warned_by/, '这半句该按场景取');
  });

  test('没人劝过的那一局，整句不印', () => {
    // 顾之然那一局本来就没有人拦过她——**那正是她最难劝的地方**，
    // 不该被一句"家里人也这么说过"的套话抹平
    const src = stripJsComments(sourceOf('review.js'));
    assert.match(src, /SCENE && SCENE\.warned_by\s*\n?\s*\?/,
      'warned_by 为空时必须走另一支，不能拼出半句话');
  });
});
