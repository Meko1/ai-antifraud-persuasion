/* 分享卡卡尾那一块：回到作品的那条路。
 *
 * 就绪度审计 P1-9：**一张传出去的图片如果不能把人带回作品，它就只是一张
 * 图片**。2026-08-29 补上二维码 + 地址之后，这一组守着它别再掉。
 *
 * 画出来长什么样测不了（canvas 在 node:vm 里是替身，见 harness.mjs 顶部
 * "测不了什么"那一段），所以这里分两路：
 *
 * · `shareLabel` 是纯函数，直接调；
 * · "卡尾还在不在"走源码断言——跟 review.test.mjs 里"复盘第一屏五块"
 *   那一组是同一个做法，理由也一样：删掉一整块不会让任何断言变红。
 *
 * 二维码本身编得对不对由 qr.test.mjs 逐格对着参考实现比，不在这儿重复。
 */

import assert from 'node:assert/strict';
import { test, describe } from 'node:test';

import { bodyOf, loadApp, sourceOf } from './harness.mjs';

const app = loadApp();
const HISTORY = sourceOf('history.js');

describe('分享卡 · 地址那一行', () => {
  test('协议头去掉，末尾那个斜杠也去掉', () => {
    // 协议头对读的人没有信息量，占的却是最贵的那一行宽度。
    // 真正编进二维码的仍然是完整地址——那一头不能省，省了扫出来不是链接
    assert.equal(app.shareLabel('http://10.20.30.40:21818/'), '10.20.30.40:21818');
    assert.equal(app.shareLabel('https://demo.example.com/w/12'), 'demo.example.com/w/12');
  });

  test('太长就截断，不让它压到二维码上', () => {
    const long = `https://demo.example.com/${'x'.repeat(80)}`;
    const label = app.shareLabel(long);
    assert.ok(label.length <= 42, `截断后应该不超过 42 个字符，实际 ${label.length}`);
    assert.ok(label.endsWith('…'), '截断了就要看得出来被截断了');
  });

  test('短地址一个字都不动', () => {
    assert.equal(app.shareLabel('demo.example.com'), 'demo.example.com');
  });
});

describe('分享卡 · 卡尾这一块不许再掉', () => {
  const body = bodyOf(HISTORY, 'makeCard');

  test('二维码、作品名、地址三样都画了', () => {
    assert.match(body, /drawQR\(/, '卡尾要画二维码');
    assert.match(body, /shareUrl\(\)/, '二维码的地址取自 shareUrl()');
    assert.match(body, /shareLabel\(/, '地址还要以人能读的形式印一行');
    assert.match(body, /AI 反诈劝阻/, '卡上要有作品名');
  });

  test('二维码画不出来时，地址那一行仍然印', () => {
    // 唯一会画不出来的情况是地址长到十版都装不下（实际碰不到）。
    // 真碰到了，卡尾要退化成"只有链接"，不能退化成"什么都没有"
    //
    // **2026-08-30 改了判据，守的东西没变。** 原先断言 `if (url) {`——
    // 那时地址一律印。上线到大赛平台之后地址去掉协议头有 61 字，
    // `shareLabel` 的 42 字上限正好截在唯一标识之前，照着敲也敲不出来，
    // 于是有码时不再印。**但"没有码就必须有地址"这条不许退**，
    // 所以断言从"条件是 url"改成"条件里必须带 !hasQR"。
    assert.match(body, /hasQR \?/, '要区分画得出来和画不出来两种写法');
    assert.match(
      body, /if \(url && !hasQR\) \{/,
      '没有码的时候地址是唯一那条路，那一支不许删',
    );
  });

  test('二维码画得出来时不印地址——一条打不开的地址不是备用路径', () => {
    // 反向守一次：别有人为了"信息更全"把地址无条件加回去。
    // 42 字上限截在 `…/ai-creator-…` 上，读的人拿它什么也做不了。
    assert.doesNotMatch(
      body, /if \(url\) \{\s*\n\s*ctx\.fillStyle = c\.note;/,
      '有码时不该再印地址',
    );
  });
});

describe('分享卡 · 地址从哪来', () => {
  test('默认取当前访问地址，不写死任何域名', () => {
    const body = bodyOf(HISTORY, 'shareUrl');
    assert.match(body, /location\.origin/, '默认值应该是当前访问地址');
    assert.match(body, /af-share-url/, '要留一个 meta 覆盖口');
    // 写死一个域名会在换机器部署时静默指错地方，而分享卡是最不容易被复核的
    // 那一块——它只在别人手机上出现
    assert.doesNotMatch(body, /https?:\/\/[\w.]+\.\w+/, '不许写死任何具体域名');
  });
});

describe('分享卡 · 卡面主角与复盘算的是同一件事', () => {
  /* 2026-08-29 换主角之后，「同一句话，换个时候说」这一块有了**两个消费方**：
   * 复盘正文与分享卡。取数因此被抽到 `contrast.js`。
   *
   * 这一组守的就是那个抽取的理由：**同一局，两处不许给出不同的倍数。**
   * 那种错直接打在"判分是可复现的"这条主张上——一个人截图发出去的数字，
   * 和他翻回正文看到的数字对不上，比两处都错更糟。 */

  const EFF = {
    // 「拆矛盾」四档落差大，「支持自主」四档几乎平——刚好够试两条分支
    expose_contradiction: { guarded: 0.6, annoyed: 0.7, shaken: 1.4, softened: 1.2 },
    support_autonomy: { guarded: 1.0, annoyed: 1.0, shaken: 1.1, softened: 1.0 },
  };

  function 一局(turns, ending = {}) {
    const app = loadApp();
    app.game.turns = turns;
    app.game.ending = { kind: 'transferred', efficacy: EFF, ...ending };
    return app;
  }

  test('拿不到效力矩阵就返回 null，两边各自退，不留空壳', () => {
    const app = 一局([], { efficacy: null });
    assert.equal(app.contrastFacts(), null);
    assert.equal(app.cardHero(), null, '卡面主角也要跟着退回旧版式');
  });

  test('挑的是"动作对、时候差得最远"的那一轮', () => {
    const app = 一局([
      // 第 2 轮：戒备时用拆矛盾，值 0.6×，而最高档 1.4× —— 落差 0.8
      { round: 2, hits: ['expose_contradiction'], efficacy: 0.6, judgedMood: 'guarded',
        utterance: '您刚才说他保本，可他又说不承诺收益，这两句能同时成立吗？',
        delta: 1, trust: 30, before: 30, pool: 0, grounded: true, lines: [], reply: '' },
      // 第 5 轮：松动时用同一把，值 1.2× —— 落差只有 0.2，不该被选中
      { round: 5, hits: ['expose_contradiction'], efficacy: 1.2, judgedMood: 'softened',
        utterance: '这笔钱转过去之后，您打算怎么把它取回来？',
        delta: 3, trust: 40, before: 37, pool: 0, grounded: true, lines: [], reply: '' },
    ]);

    const f = app.contrastFacts();
    assert.equal(f.kind, 'gap');
    assert.equal(f.round, 2, '该讲落差最大的那一轮，不是最后一轮');
    assert.equal(f.val, 0.6);
    assert.equal(f.bestVal, 1.4);
    assert.equal(f.bestMood, 'shaken');
    assert.equal(f.times, '2.3', '1.4 / 0.6 = 2.3');
  });

  test('卡上那个倍数，就是复盘那个倍数', () => {
    const app = 一局([
      { round: 3, hits: ['expose_contradiction'], efficacy: 0.7, judgedMood: 'annoyed',
        utterance: '他为什么一定要今天三点前？',
        delta: 1, trust: 30, before: 30, pool: 0, grounded: true, lines: [], reply: '' },
    ]);

    const f = app.contrastFacts();
    const hero = app.cardHero();
    assert.equal(hero.hero, `差 ${f.times} 倍`, '卡面主角必须直接取自同一份取数');
    assert.match(hero.eyebrow, /第 3 轮/);
    assert.equal(hero.quote, '他为什么一定要今天三点前？', '卡上印的是玩家自己那句原话');
  });

  test('一把钥匙都没命中也有得讲（P0-4 那条：最需要解释的那一半人）', () => {
    // 最低一档仍占 53.4%、novice 胜率 0.0%，"一把没中"正是评委最可能打出的那局
    const app = 一局([
      { round: 1, hits: [], efficacy: null, judgedMood: 'guarded', utterance: '你别转了',
        delta: -2, trust: 28, before: 30, pool: 0, grounded: false, lines: [], reply: '' },
    ]);

    const f = app.contrastFacts();
    assert.equal(f.kind, 'nokey');
    assert.equal(f.key, 'expose_contradiction', '该挑四档落差最大的那一把举例');
    assert.equal(f.times, '2.3');
    assert.ok(app.cardHero(), '这一档卡面照样有主角，不退回金额');
  });
});

describe('分享卡 · 原话断行', () => {
  const app = loadApp();
  /** 一个够用的量文字替身：一个字算 10 宽。canvas 在沙箱里是替身，量不了真宽度。 */
  const ctx = { measureText: (s) => ({ width: [...s].length * 10 }) };
  /** 沙箱里造出来的数组来自**另一个 realm**，原型不是本地那个 Array，
   *  `deepStrictEqual` 会报"结构相同但不是同一个引用"。搬回本地再比。 */
  const 折 = (...args) => Array.from(app.wrapText(ctx, ...args));

  test('放得下就一行不折', () => {
    assert.deepEqual(折('短句', 100, 2), ['短句']);
  });

  test('放不下就折，按字断——中文没有词边界，按空格断等于不折', () => {
    assert.deepEqual(折('一二三四五六', 30, 2), ['一二三', '四五六']);
  });

  test('超出行数上限要看得出来被截断了', () => {
    const lines = app.wrapText(ctx, '一二三四五六七八九十', 30, 2);
    assert.equal(lines.length, 2);
    assert.ok(lines[1].endsWith('…'), `末行该带省略号，实际是 ${lines[1]}`);
  });
});
