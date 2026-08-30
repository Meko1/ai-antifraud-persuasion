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
import test, { describe } from 'node:test';

import { bodyOf, loadApp, sourceOf, turn } from './harness.mjs';

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
  const source = sourceOf('review.js');
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
      // 2026-08-30：第 4 块从「投顾侧处置清单」换成「回到你自己那一笔」。
      // 投顾那五条没删，搬进了折叠——`class="disposal"` 两处都有，
      // 拿它当标记已经分不清是哪一块了，改认 id
      ['回到你自己那一笔', 'id="mirrorLead"'],
      ['分享卡', 'id="makeCard"'],
    ]) {
      assert.ok(template.includes(marker), `第一屏少了「${what}」`);
    }
  });
});

describe('转账这一档：戒备与烦躁不是同一件事', () => {
  // 阶梯自己声称量的是"他最后有多信你"，却把这两档压成了同一句话。
  // 复盘把这条信息说回来——**但不改分档、不改金额**：
  // 2026-08-25 评估过把烦躁档划进「拖住」，被否掉了（他还烦躁着就说
  // "你争到了时间"，是拿一句不成立的话去换一个好看的分布）。
  function transferred(mood) {
    const app = fresh([turn({ round: 12, trust: mood === 'guarded' ? 18 : 36 })], SCENE_CHEN);
    app.game.ending = { kind: 'transferred' };
    app.game.mood = mood;
    return app;
  }

  test('他从头到尾防着你', () => {
    const copy = transferred('guarded').verdictCopy();
    assert.match(copy, /防着你/);
    assert.doesNotMatch(copy, /没有松口/);
  });

  test('他一直在说话，只是没松口', () => {
    const copy = transferred('irritated').verdictCopy();
    assert.match(copy, /没有松口/);
    assert.doesNotMatch(copy, /防着你/);
  });

  test('两种说法都仍然是最低一档，钱照样全转走', () => {
    for (const mood of ['guarded', 'irritated']) {
      const app = transferred(mood);
      assert.equal(app.reviewKind(), 'transferred');
      assert.match(app.resultAmount('transferred').value, /¥0/);
    }
  });
});

describe('复盘只对这一局说话，不许诺下一局', () => {
  /* POSITIONING「成功标准·对产品」：**训练可以打第二局，干预只有一次机会。**
   *
   * 2026-08-30 之前复盘有三处对玩家说「下一局试着…」，那是训练器定位的语气
   * 残留——转向 C 端之后坐在这儿的是正要转账被拦下来的人，他不会有下一局。
   * 许诺一个不会发生的下一次，比不给建议更糟：它把"这一局你差在哪"
   * 换成了"你以后注意点"。
   *
   * **本机记录那一块（history.js）不在此列**：它整块的前提就是跨局比较，
   * 只在真的打过第二局时才有内容，那里说「下一局」是准确的。 */

  /** 去掉注释行之后的源码。断言的是**给玩家看的字符串**，
   *  不是解释这条规矩的注释本身（那条注释里就带着这三个字）。 */
  function 只留代码(src) {
    return src.split('\n')
      .filter((line) => !/^\s*(\/\/|\/?\*)/.test(line))
      .join('\n');
  }

  test('review.js 面向玩家的文案里没有「下一局」', () => {
    const 代码 = 只留代码(sourceOf('review.js'));
    const 命中 = 代码.split('\n').filter((l) => l.includes('下一局'));
    assert.deepEqual(命中, [],
      `复盘又许诺了下一局：\n${命中.join('\n')}\n`
      + '——干预只有一次机会，改成讲这一局差在哪');
  });

  test('本机记录那一块仍然可以说「下一局」', () => {
    // 反向钉一下，免得哪天有人拿上面那条规矩去把这一句也删了
    assert.match(sourceOf('history.js'), /下一局打完这里会有对比/);
  });
});

describe('回到你自己那一笔', () => {
  /* 冷开场让玩家自己签一笔、被拦下、然后「坐到对面」。
   * 2026-08-30 之前，复盘从头到尾没有一处回到那一笔——**环开了没合上**，
   * "角色对调"只完成了一半：他替别人想了三分钟，没有一秒被请回自己身上。
   * 这一块就是那个缺口，它是全作品唯一把心理反思落回本人的地方。 */

  const 源 = sourceOf('review.js');

  test('引用的是玩家自己挣到分的那几句，不是产品替他写的金句', () => {
    const body = bodyOf(源, 'paintMirror');
    assert.match(body, /scoredTurns\(\)/, '要从这一局真实说过的话里取');
    assert.match(body, /t\.delta > 0/, '只取真正推动过他的那几句');
    assert.match(body, /slice\(0, 3\)/, '最多三句');
    // 换成一句漂亮话，这一块立刻退化成又一段鸡汤
    assert.doesNotMatch(body, /['"`][^'"`]{12,}吗？['"`]\s*\]/, '不许在这里写死一串金句');
  });

  test('一句都没挣到分也有得说——那是最常见的一局', () => {
    // balance_sim：最低一档仍占 53.4%、novice 胜率 0.0%。
    // 这一档留白，等于把最需要被说到的那一半人跳过去（P0-4 同款错误）
    const body = bodyOf(源, 'paintMirror');
    assert.match(body, /fallback/, '没命中时要有退路');
    assert.match(body, /anchor_real_purpose/, '退路取的是钥匙自己的原话');
    assert.doesNotMatch(body, /你什么都没做对/, '不许对他说这种话');
  });

  test('没演过冷开场就不许说"三分钟前你也按了确认"', () => {
    // 那一屏一个会话只演一次（换客户、再开一局都不重演），隐私模式下
    // 还可能从来没写进 sessionStorage。**不许对玩家断言一件没发生的事。**
    const body = bodyOf(源, 'paintMirror');
    assert.match(body, /transferSeen\(\)/, '要先问一句他到底演没演过');
    const 断言句 = body.match(/三分钟前[^`']*/);
    assert.ok(断言句, '找不到那句开场白');
    assert.match(body, /transferSeen\(\)\s*\?/, '那句话必须挂在条件后面，不能无条件拼上去');
  });

  test('四条动作全是他一个人就能做的', () => {
    // 从 `mirrorLead` 切起，**不从块标题切**：标题上面那段注释里正好把
    // 「让客户」「陪着他」当反例引了一遍，从那儿切会把注释算进来
    const 第一屏 = 源.slice(源.indexOf('id="mirrorLead"'), 源.indexOf('result-actions'));
    for (const 词 of ['让客户', '本机构', '陪着他', '约下一次回访', '你所在机构']) {
      assert.ok(!第一屏.includes(词),
        `「${词}」是投顾侧的动作，不该出现在这一块——它属于折叠里那份清单`);
    }
    for (const 动作 of ['先不按那个确认', '96110', '人工客服']) {
      assert.ok(第一屏.includes(动作), `少了「${动作}」`);
    }
  });

  test('投顾那五条没被删，只是搬进了折叠', () => {
    // 对局中玩家确实在扮投顾，那五条对他仍然成立。搬家不是删除
    const 折叠 = 源.slice(源.indexOf('evidence-content'));
    assert.match(折叠, /在系统里留痕并上报/, '投顾侧清单被删了');
    assert.match(折叠, /如果你是他的投顾/, '搬过去之后标题要把人称说清楚');
  });
});
