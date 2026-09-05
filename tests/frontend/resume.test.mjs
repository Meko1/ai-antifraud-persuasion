/* 断线续局（static/app.js 的 saveGame / loadSaved / resumeGame）。
 *
 * ## 这份文件为什么存在
 *
 * **刷新一次整局就没了。** 在旧定位（投顾训练器）下这只是个瑕疵——受训者
 * 坐在工位上，不会中途切走。转向 C 端异动干预之后它是硬伤：用户正要转账时
 * 被弹出这一屏，切出去看一眼真实的转账页面再回来是最自然不过的动作，
 * 而手机在内存吃紧时会把后台标签整个重载。
 *
 * 回来发现要从头再打十二轮，他不会从头再打——他会去把那笔钱转了。
 * **完成率是转向之后定下的护栏指标之一，这一条直接吃掉它。**
 *
 * ## 第一版栽在哪儿（下面第三组就是它的回归测试）
 *
 * 第一版把整个续局模块放在了文件末尾，而调用它的那行
 * `const savedGame = loadSaved()` 在文件中段。`RESUME_KEY` 是个 `const`，
 * 于是 `loadSaved()` 在模块求值时撞上**暂时性死区**，抛
 * `ReferenceError: Cannot access 'RESUME_KEY' before initialization`。
 *
 * 而 `loadSaved` 自己有一个 `catch { return null; }`——**我写的那个 catch
 * 把这个错误整个吞了**，于是它安静地"没有存档"，每次都开新局。
 * 真机上点开、打一轮、刷新，才发现续局根本没生效。
 *
 * **兜底的 try/catch 会把初始化顺序错误伪装成"功能正常但没数据"**，
 * 这是这一类 bug 最难查的形态。沙箱看不见 TDZ（它只会得到同一个 null），
 * 所以那一组改成直接读源码断言声明顺序——与 review.test.mjs 末尾
 * 那条"第一屏五块"用的是同一个办法。
 */

import assert from 'node:assert/strict';
import test, { describe } from 'node:test';

import { SOURCE, loadApp, turn } from './harness.mjs';

const SCENE = {
  id: 'chen',
  peer: '老陈',
  pronoun: '他',
  initial: '陈',
  ping: '陈叔，方便说句话吗？',
  client: { name: '陈国栋', facts: [] },
  money: { total: 300000, test_transfer: 20000 },
  endings: {},
  phone: [],
};

/** 一局打到一半的样子。 */
function 打到一半(app, 轮数 = 2) {
  app.SCENE = SCENE;
  Object.assign(app.game, {
    token: 'signed.token.value',
    opening: '你们那边还能瞅见啊。',
    trust: 44,
    mood: 'irritated',
    threshold: 80,
    maxRounds: 10,
    remaining: 10 - 轮数,
    notice: '对方是虚构客户……',
    turns: Array.from({ length: 轮数 }, (_, i) =>
      turn({ round: i + 1, utterance: `第 ${i + 1} 句`, reply: `回第 ${i + 1} 句` })),
    ending: null,
  });
  return app;
}

describe('存下来的那一局要能原样读回来', () => {
  test('往返一趟，轮次与令牌都在', () => {
    const app = 打到一半(loadApp(), 3);
    app.saveGame();

    const saved = app.loadSaved();
    assert.ok(saved, '存了却读不回来');
    assert.equal(saved.game.token, 'signed.token.value');
    assert.equal(saved.game.turns.length, 3);
    assert.equal(saved.game.remaining, 7);
    assert.equal(saved.scene.id, 'chen');
  });

  test('那句告知也要一起存', () => {
    // 留存开着和关着说的**不是同一句话**（ADR-0006），而续局时拿不到
    // 服务端那一句。不存的话，页面上会退回静态兜底——说错的那一种错。
    const app = 打到一半(loadApp());
    app.game.notice = '……并会脱敏留存 90 天用于改进模型。';
    app.saveGame();

    assert.match(app.loadSaved().notice, /留存 90 天/);
  });

  test('还没开局就不存', () => {
    // 没有令牌的"存档"续不出任何东西，只会让启动那一步白走一趟
    const app = loadApp();
    app.game.token = '';
    app.saveGame();

    assert.equal(app.loadSaved(), null);
  });
});

describe('过期与脏数据一律当没存过', () => {
  test('超过令牌有效期就不续', () => {
    // 令牌自带 2 小时有效期。拿一个服务端必然拒绝的令牌去续局，
    // 换来的只是一句玩家看不懂的错误提示——不如干脆重开
    const app = 打到一半(loadApp());
    app.saveGame();

    const saved = JSON.parse(app.sessionStorage.getItem('aap.game.v1'));
    saved.savedAt = Date.now() - 3 * 60 * 60 * 1000;
    app.sessionStorage.setItem('aap.game.v1', JSON.stringify(saved));

    assert.equal(app.loadSaved(), null);
    assert.equal(app.sessionStorage.getItem('aap.game.v1'), null, '过期的要顺手清掉');
  });

  test('残缺的存档不续', () => {
    const app = loadApp();
    for (const bad of ['{', 'null', '{"game":{}}', '{"game":{"token":"t"}}']) {
      app.sessionStorage.setItem('aap.game.v1', bad);
      assert.equal(app.loadSaved(), null, bad);
    }
  });
});

describe('声明顺序：loadSaved 在被调用那一刻必须已经初始化', () => {
  /* 沙箱看不见 TDZ——`loadSaved()` 抛 ReferenceError 之后，它自己的
     catch 会把结果变成 null，和"真的没有存档"完全一样。所以这一组
     直接读源码断言顺序。丑，但它是唯一拦得住第一版那个 bug 的写法。

     2026-08-24 拆分模块之后这一条**变强了**：`RESUME_KEY` 在 state.js，
     调用点在 opening.js，而 opening.js import state.js —— ES module 保证
     被依赖的先求值完。这里读的 SOURCE 是按同一个顺序拼起来的，
     所以它同时钉住了拼接顺序（harness.mjs 的 MODULES）没被人改反。 */
  const src = SOURCE;

  test('RESUME_KEY 声明在 loadSaved 的调用点之前', () => {
    const 声明 = src.indexOf('const RESUME_KEY');
    const 调用 = src.indexOf('savedGame = loadSaved()');

    assert.ok(声明 >= 0, '找不到 RESUME_KEY 的声明');
    assert.ok(调用 >= 0, '找不到启动时那次 loadSaved() 调用');
    assert.ok(
      声明 < 调用,
      'RESUME_KEY 声明在调用点之后 —— loadSaved() 会撞暂时性死区，'
      + '而它自己的 catch 会把这个错误吞成"没有存档"，续局静默失效',
    );
  });

  test('TTL 也一样', () => {
    assert.ok(
      src.indexOf('const RESUME_TTL_MS') < src.indexOf('savedGame = loadSaved()'),
    );
  });
});

describe('「开始一位新客户」必须先把存档清掉', () => {
  test('不清的话它会把刚打完那局又续回来', () => {
    // 它走的是 location.reload()，而启动那一步现在优先续局。
    // 这个按钮的语义是"换一个客户"，续回旧局是它能犯的最糟的错
    const app = 打到一半(loadApp());
    app.saveGame();
    assert.ok(app.loadSaved(), '前置条件：这时候应该是有存档的');

    app.startNewClient();

    assert.equal(app.loadSaved(), null);
    assert.equal(app.location._reloaded, true, '清完还是要真的重载');
  });

  test('源码里不许再有不清存档就 reload 的出路', () => {
    // 令牌过期、令牌重放那两条出路也走 reload。漏掉任何一条，
    // 玩家点"重开一局"会原样回到那个再也发不出去的局面
    const src = SOURCE;
    const 裸的 = src
      .split('\n')
      .filter((line) => {
        const 剥掉注释 = line.split('//')[0];
        return /location\.reload\(\)/.test(剥掉注释)
          && !/function startNewClient/.test(剥掉注释);
      })
      // startNewClient 自己那一行是唯一允许的
      .filter((line) => !/^\s*location\.reload\(\);\s*$/.test(line.split('//')[0]));

    assert.deepEqual(裸的, [], `还有不清存档就重载的地方：\n${裸的.join('\n')}`);
  });
});
