/* 复盘的判断逻辑。跑法：`node --test tests/frontend/`
 *
 * ## 这份文件为什么存在
 *
 * 后端 513 个测试，前端 0 个——而**复盘承载着这个作品全部的教学价值**。
 * 代价是实打实的：`degraded`（网关抖了、这一轮根本没判成）服务端一直在
 * 下发，前端一次都没读过，于是它在能力画像里和"玩家白说了一轮"长得一模
 * 一样，还占着分母。网关抖一下，账记在人头上，玩家连申辩的依据都没有。
 * 那个 bug 潜伏了很久，就是因为这一层没有任何自动化守护。
 *
 * 下面第一组测的就是它。
 *
 * 沙箱怎么搭的、测得了什么测不了什么，见 harness.mjs 顶部。
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import test, { describe } from 'node:test';

import { APP_JS, loadApp, turn } from './harness.mjs';

/** 每个用例一份干净的作用域：app.js 的状态挂在顶层，用例之间会互相污染。 */
function fresh(turns = [], scene = null) {
  const app = loadApp();
  app.game.turns = turns;
  if (scene) app.SCENE = scene;
  return app;
}

const SCENE_CHEN = {
  name: '荐股局',
  pronoun: '他',
  client: { name: '老陈' },
  money: { total: 300000, test_transfer: 100000 },
  endings: {},
};

describe('scoredTurns：降级轮不许进任何一个分母', () => {
  test('判成了的才算', () => {
    const app = fresh([
      turn({ round: 1, hits: ['socratic_question'], delta: 5 }),
      turn({ round: 2, degraded: true, hits: [], delta: 0 }),
      turn({ round: 3, hits: ['scold'], delta: -4 }),
    ]);
    assert.equal(app.scoredTurns().length, 2);
    assert.deepEqual(app.scoredTurns().map((t) => t.round), [1, 3]);
  });

  test('整局全降级时分母是 0，不是 12', () => {
    // 网关整段挂掉的那一局。分母若还按 game.turns 算，
    // 玩家会看到一份"七把钥匙一把没用"的能力画像，而他其实说满了十二轮
    const app = fresh([1, 2, 3].map((round) => turn({ round, degraded: true })));
    assert.equal(app.scoredTurns().length, 0);
    assert.equal(app.game.turns.length, 3, '逐轮清单仍要显示这几轮，它们确实发生过');
  });
});

describe('breachTurns：合规红线与话术失误不是一类东西', () => {
  test('只数合规红线', () => {
    const app = fresh([
      turn({ round: 1, hits: ['scold'] }),
      turn({ round: 2, hits: ['unlicensed_advice'] }),
      turn({ round: 3, hits: ['guaranteed_return', 'bare_assertion'] }),
    ]);
    assert.deepEqual(app.breachTurns().map((t) => t.round), [2, 3]);
  });

  test('一把红线都没踩就是空', () => {
    const app = fresh([turn({ hits: ['preach'] })]);
    assert.deepEqual(app.breachTurns(), []);
  });

  test('降级轮不影响它', () => {
    // 这一条是刻意与 scoredTurns 相反的：合规红线数的是"你说过这句话"，
    // 而不是"这一轮判成没判成"。踩了就是踩了
    const app = fresh([turn({ hits: ['unlicensed_advice'], degraded: false })]);
    assert.equal(app.breachTurns().length, 1);
  });
});

describe('reviewRows：三行从真实对局里算，不按结局写死', () => {
  test('做对了那一行报的是真正挣了分的那把钥匙', () => {
    const app = fresh([
      turn({ round: 1, hits: ['anchor_real_purpose'], delta: 12 }),
      turn({ round: 2, hits: ['socratic_question'], delta: 3 }),
    ], SCENE_CHEN);
    const [[label, text]] = app.reviewRows();
    assert.equal(label, '做对了');
    assert.match(text, /锚定真实用途/);
    assert.match(text, /12 分/);
  });

  test('一分都没挣到就说实话，不编一句好听的', () => {
    const app = fresh([turn({ hits: ['scold'], delta: -5 })], SCENE_CHEN);
    const [[, text]] = app.reviewRows();
    assert.doesNotMatch(text, /用得最见效/);
  });

  test('踩过的坑优先于没用过的钥匙', () => {
    const app = fresh([turn({ hits: ['scold', 'preach'], delta: -5 })], SCENE_CHEN);
    const [, [label, text]] = app.reviewRows();
    assert.equal(label, '可改进');
    assert.match(text, /责骂/);
    assert.match(text, /说教/);
  });

  test('合规那一行与合规红线卡同源', () => {
    const app = fresh([turn({ hits: ['unlicensed_advice'] })], SCENE_CHEN);
    const [, , [label, text, bad]] = app.reviewRows();
    assert.equal(label, '合规');
    assert.match(text, /1 次/);
    assert.equal(bad, true, '踩了线要标红，这一行是全作品唯一与输赢无关的判定');
  });

  test('没踩线时那一行不标红', () => {
    const app = fresh([turn({ hits: ['socratic_question'], delta: 4 })], SCENE_CHEN);
    const [, , [, , bad]] = app.reviewRows();
    assert.equal(bad, false);
  });
});

describe('resultAmount：结算卡上那个大数', () => {
  test('劝住了是全额', () => {
    const app = fresh([], SCENE_CHEN);
    assert.match(app.resultAmount('persuaded').value, /300,000/);
  });

  test('拦下扣掉已经转出去的那一笔', () => {
    const app = fresh([], SCENE_CHEN);
    assert.match(app.resultAmount('intercepted').value, /200,000/);
  });

  test('被拉黑不给数字', () => {
    // 联系中断之后他转没转、转了多少，投顾这一侧根本看不见。
    // 编一个数出来，等于把作品最核心那条设定（看得见账户、看不见生活）自己拆了
    const app = fresh([], SCENE_CHEN);
    assert.equal(app.resultAmount('blacklisted').value, '状态未知');
  });
});

/* ── 第一屏五块，硬上限 ─────────────────────────────────────────────────
 *
 * PIVOT-C-END §3.2 把复盘第一屏定死在五块以内。它是"能不能用"的前提：
 * 十三块的时候，玩家看不懂就直接忽略，剩下十二块一起废掉。
 *
 * **这一组是读源码里的模板，不是读渲染出来的 DOM**——没有 jsdom 就没有
 * DOM（见 harness.mjs 顶部第 1 条）。它因此挡不住"块还在但样式塌了"，
 * 挡得住的是这条上限被不声不响地加回去，而那正是它历史上出事的方式：
 * 8-22 一天里往里加了两块，没有任何东西拦一下。
 */
describe('复盘第一屏：五块是硬上限', () => {
  const source = fs.readFileSync(APP_JS, 'utf8');
  const template = source.slice(
    source.indexOf('<div class="review-body">'),
    source.indexOf('<div class="result-actions">'),
  );

  test('模板切得出来', () => {
    assert.ok(template.length > 0, 'openReview 的模板结构变了，这一组测试要跟着改');
  });

  test('第一屏的块数不超过五', () => {
    // 类名后面那一位不能省：少了它，`class="group-title"` 也会被数进来
    const blocks = template.match(/<(section|div) class="(summary|group)["\s]/g) || [];
    // summary 与紧跟着的 scoreline 是同一块（结算卡），scoreline 不带 group/summary 类，
    // 因此自然不计入
    assert.ok(
      blocks.length <= 5,
      `第一屏有 ${blocks.length} 块，上限 5（PIVOT-C-END §3.2）。`
      + '要加新东西，先把一块收进「详细复盘」折叠里。',
    );
  });

  test('砍掉的是收进折叠，不是删掉', () => {
    // 逐轮三段账、七把钥匙条、本机记录对认真的人仍然有价值，
    // 只是不该挡在第一屏。删了就是另一回事了
    for (const id of ['#keyBars', '#roundsList', '#historyWrap', '#statsWrap', '#chart']) {
      assert.ok(source.includes(id.slice(1)), `${id} 不见了——那不是折叠，是删除`);
      assert.ok(
        !template.includes(`id="${id.slice(1)}"`),
        `${id} 还留在第一屏上`,
      );
    }
  });

  test('那五块一块都不少', () => {
    for (const [what, marker] of [
      ['结算卡', 'class="summary'],
      ['同一句话换个时候说', 'id="contrastWrap"'],
      ['手机揭晓', 'id="phoneTitle"'],
      ['处置清单', 'class="disposal"'],
      ['分享卡', 'id="makeCard"'],
    ]) {
      assert.ok(template.includes(marker), `第一屏少了「${what}」`);
    }
  });
});
