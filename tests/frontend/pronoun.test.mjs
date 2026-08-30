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

import { MODULES, STATIC, sourceOf } from './harness.mjs';

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
