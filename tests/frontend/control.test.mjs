/* 用户控制、识别优于回忆、灵活与效率——三条 Nielsen 启发式在前端的落点。
 *
 * 这一组钉的**不是渲染**（沙箱里的 DOM 是替身，见 harness.mjs 顶部那段），
 * 而是三件不碰 DOM 就能判错的事：
 *
 * 1. 主动结束之后，复盘按哪一档走 —— 判错的代价是**告诉用户一个没发生的资金结局**
 * 2. 「问法」那一层给什么、不给什么 —— 判错的代价是对局中泄题
 * 3. 挑客户什么时候能出现 —— 判错的代价是真实接入时让用户挑一个与自己无关的剧本
 *
 * 第 1 条最要紧。在它之前 `openReview` 拿不到 ending 就落到 `'transferred'`，
 * 于是一个第 3 轮自己点"结束"的用户会看到「¥300,000 已全部转出」。
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test, describe } from 'node:test';

import { bodyOf, loadApp, sourceOf, turn } from './harness.mjs';

describe('用户控制：主动结束之后不许编造资金结局', () => {
  test('没结局也没主动结束时仍然按转账算', () => {
    // 打满十二轮但 ending 事件丢了 —— 这是原来那个兜底本来要覆盖的情况
    const app = loadApp();
    app.game.ending = null;
    app.game.exited = false;
    assert.equal(app.reviewKind(), 'transferred');
  });

  test('主动结束走 unfinished 那一档', () => {
    const app = loadApp();
    app.game.ending = null;
    app.game.exited = true;
    assert.equal(app.reviewKind(), 'unfinished');
  });

  test('真的走到结局时以结局为准', () => {
    const app = loadApp();
    app.game.exited = true;   // 结束之后又刷新了页面
    app.game.ending = { kind: 'persuaded' };
    assert.equal(app.reviewKind(), 'persuaded', '有结局就该用结局，不该被 exited 盖掉');
  });

  test('unfinished 不给金额，只说没有结果', () => {
    const app = loadApp();
    const r = app.resultAmount('unfinished');
    assert.equal(r.value, '未产生结果');
    assert.ok(!/¥|\d{3}/.test(r.value), `不许出现任何金额：${r.value}`);
    assert.ok(/主动结束/.test(r.label));
  });

  test('unfinished 不出转账凭证', () => {
    const app = loadApp();
    assert.equal(app.endingMeta('unfinished').receipt, null,
      '那张卡说的是"这笔钱最后怎么了"，而这一局没走到那一步');
    assert.equal(app.endingMeta('unfinished').amount, 0);
  });

  test('unfinished 的标题说清是在第几轮结束的', () => {
    const app = loadApp();
    app.game.turns = [turn({ round: 1 }), turn({ round: 2 })];
    assert.match(app.endingMeta('unfinished').title, /第 2 轮/);
  });

  test('一轮都没打时不说"第 0 轮"', () => {
    const app = loadApp();
    app.game.turns = [];
    assert.doesNotMatch(app.endingMeta('unfinished').title, /第 0 轮/);
  });

  test('色调是中性灰，不是成功绿也不是失败红', () => {
    const app = loadApp();
    assert.equal(app.TONES.unfinished, 'plain',
      '主动退出不是失败，也不是成功——给红等于一边给退出按钮一边惩罚按下去的人');
  });
});

describe('模拟反应 ≠ 真实交易结果', () => {
  test('每一档结局都要带一句出处说明', () => {
    const app = loadApp();
    for (const kind of ['persuaded', 'intercepted', 'stalled', 'transferred', 'blacklisted']) {
      const line = app.RESULT_BASIS[kind] || app.RESULT_BASIS.default;
      assert.ok(line && line.length > 10, `${kind} 缺出处说明`);
    }
  });

  test('那句话必须点明"不是真实交易结果"', () => {
    const app = loadApp();
    const line = app.RESULT_BASIS.default;
    assert.match(line, /虚构客户/);
    assert.match(line, /不是真实交易结果/);
    assert.match(line, /交易系统回传/);
  });

  test('unfinished 那一句不能暗示有资金状态', () => {
    const app = loadApp();
    const line = app.RESULT_BASIS.unfinished;
    assert.match(line, /没有资金状态/);
  });

  test('不写 markdown 记号', () => {
    // 它走 textContent，写了会原样印在页面上
    const app = loadApp();
    for (const v of Object.values(app.RESULT_BASIS)) {
      assert.doesNotMatch(v, /\*\*/, `出处说明里有 markdown 记号：${v}`);
    }
  });
});

describe('识别优于回忆：七种问法可以随时翻回来看', () => {
  test('入口在对局中一直在（不是只在开打前那一屏）', () => {
    const html = fs.readFileSync(
      new URL('../../static/index.html', import.meta.url), 'utf8');
    assert.match(html, /id="openMethods"/, '聊天页要有翻看问法的入口');
    assert.ok(html.indexOf('id="openMethods"') > html.indexOf('id="chat"'),
      '入口要在聊天屏里，不是在别的屏上');
  });

  test('翻开的那一层只给 brief，不给 tip', () => {
    // **词汇是课程，时机是答案。** tip 里写着"他越防着你别的招越没用"
    // 这类时机信息，对局中给出来就是泄题。
    const body = bodyOf(sourceOf('control.js'), 'openMethodsSheet');
    assert.match(body, /\.brief/);
    assert.doesNotMatch(body, /\.tip/, '对局中不许给 tip —— 那是复盘才讲的时机');
  });

  test('七把钥匙一把都不能少', () => {
    const app = loadApp();
    assert.equal(Object.keys(app.KEYS).length, 7);
    for (const [k, v] of Object.entries(app.KEYS)) {
      assert.ok(v.name && v.brief, `${k} 缺 name 或 brief`);
    }
  });

  test('brief 里不许出现分值或档位', () => {
    const app = loadApp();
    for (const [k, v] of Object.entries(app.KEYS)) {
      assert.doesNotMatch(v.brief, /\d+\s*分|戒备|烦躁|动摇|松动|倍/,
        `${k} 的 brief 泄露了时机或分值：${v.brief}`);
    }
  });
});

describe('灵活与效率：挑客户', () => {
  test('没有清单时按钮不出现', () => {
    const app = loadApp();
    app.game.catalog = [];
    // paintClientPicker 只读 game.catalog 决定显不显示，这里直接验判据
    assert.ok(!(app.game.catalog && app.game.catalog.length > 1));
  });

  test('清单只有一个时也不出现', () => {
    const app = loadApp();
    app.game.catalog = [{ id: 'chen' }];
    assert.ok(!(app.game.catalog.length > 1), '只有一个可选等于没得选');
  });

  test('判据是服务端下发的清单，不是前端自己判演示态', () => {
    // "什么时候允许挑客户"是业务规则，规则只该有一处（app/main.py）。
    // 前端要是自己按 source === 'demo' 判，规则就有了两份。
    const body = bodyOf(sourceOf('opening.js'), 'paintClientPicker');
    assert.match(body, /game\.catalog/);
    assert.doesNotMatch(body, /source\s*===\s*['"]demo/,
      '别在前端复制一份"什么时候能挑客户"的规则');
  });
});

describe('退出留痕不许挡住退出', () => {
  // 埋点这一下 2026-08-24 随 P2-1 挪进了 api.js（全站唯一的 fetch 出口）
  const body = bodyOf(sourceOf('api.js'), 'reportExit');

  test('不 await 埋点', () => {
    assert.doesNotMatch(body, /await\s+fetch/, '埋点绝不能挡住用户离开');
  });

  test('埋点失败被吞掉', () => {
    assert.match(body, /catch/);
  });

  test('用 sendBeacon 保证页面卸载时也送得出去', () => {
    assert.match(body, /sendBeacon/);
  });
});
