/* 卡住时那三条兜底句挑得对不对。跑法：`node --test tests/frontend/`
 *
 * ## 这份文件为什么存在
 *
 * 2026-09-07 实测顾之然那一局的第 1 轮（她的开场白是「怎么，赎回还要说明
 * 用途？」），输入框上方给的三条是：
 *
 *   ① 这笔钱您本来是留着做什么用的？        ← 正撞在她刚顶回来的那句上
 *   ② 您自己核实过对方是谁吗，怎么核实的？
 *   ③ 您前面说的两件事，我核对一下，是不是有点对不上？
 *                                          ← 她统共说了一句，没有"两件事"
 *
 * 第三条是硬伤：**这一招要求的材料这一刻根本不存在**。玩家照着发出去，
 * 说的是一句凭空断言——而空口断言正是这一局要扣分的三种失误之一。
 * 兜底句是给"想不出词"的人一句能发的话，不该反过来坑他。
 *
 * 原因是老实现只按 `turns.length % 2` 整排翻，**从不看对面说过什么**。
 * 现在判据写在 `keys.js` 的 `STUCK_HINTS` 上（needs / clash / cue / moods），
 * 由 `hints.js` 的 `stuckHints()` 执行。下面钉的就是那几条判据。
 *
 * 沙箱怎么搭的、测得了什么测不了什么，见 harness.mjs 顶部。
 */

import assert from 'node:assert/strict';
import test, { describe } from 'node:test';

import { loadApp, turn } from './harness.mjs';

/** 每个用例一份干净的作用域。`opening` 是她的开场白——一轮都没打过时，
 *  "她刚说的那句"就是它。 */
function fresh({ opening = '怎么，赎回还要说明用途？', turns = [], mood = 'irritated' } = {}) {
  const app = loadApp();
  app.game.opening = opening;
  app.game.turns = turns;
  app.game.mood = mood;
  return app;
}

/** 她回了什么、我说了什么，只这两样与挑句有关。 */
function 一轮(utterance, reply) {
  return turn({ utterance, reply, lines: [reply] });
}

/** 沙箱里的数组过一遍 `Array.from`：`vm` 那一侧的 Array 与这一侧不是同一个
 *  原型，直接 `deepEqual([], [])` 会以"结构相同但不是同一个引用"报红。 */
const 本地 = (arr) => Array.from(arr);

const 拆矛盾 = (app) => 本地(app.STUCK_HINTS
  .filter((h) => h.key === 'expose_contradiction').map((h) => h.text));

describe('句库本身', () => {
  test('只出三把「本能」钥匙', () => {
    const app = loadApp();
    const keys = [...new Set(本地(app.STUCK_HINTS).map((h) => h.key))];
    assert.deepEqual(keys.sort(), [
      'anchor_real_purpose', 'expose_contradiction', 'socratic_question',
    ], '倾听 / 支持自主 / 确认理解 / 有据告知是学来的招，'
      + '一个正卡住的人不会想到说「转不转是您的钱，我不替您做主」');
  });

  test('每一条都是问句', () => {
    // 兜底句发出去是要判分的。陈述句在这套闭集里最容易落进「说教」或
    // 「空口断言」——那两样都扣分，而玩家是照着我们给的话说的
    const app = loadApp();
    const 不是问句 = 本地(app.STUCK_HINTS).filter((h) => !h.text.endsWith('？'));
    assert.deepEqual(不是问句.map((h) => h.text), []);
  });

  test('每一条都认得出是哪把钥匙，且钥匙在闭集里', () => {
    const app = loadApp();
    const 野的 = 本地(app.STUCK_HINTS).filter((h) => !(h.key in app.KEYS));
    assert.deepEqual(野的.map((h) => h.key), []);
  });
});

describe('材料不够的那一招不给（needs）', () => {
  test('第 1 轮不出「指出内部矛盾」——她还没有两件事可核对', () => {
    const app = fresh();
    const 给的 = 本地(app.stuckHints());
    const 越界 = 给的.filter((t) => 拆矛盾(app).includes(t));
    assert.deepEqual(越界, [],
      '她统共只说了一句开场白，让玩家去指她自相矛盾就是教他空口断言');
  });

  test('少的那一格由别把钥匙补上，仍然是三条', () => {
    // 三个筹码是这一排的形状。缺一格不如换一句
    assert.equal(fresh().stuckHints().length, 3);
  });

  test('她说够了之后拆矛盾才回来', () => {
    const app = fresh({
      turns: [
        一轮('您是怎么知道这个的？', '群里老师带的，跟着走就行。'),
        一轮('这钱您留着做什么用的？', '我自己的钱，用不着报备。'),
      ],
    });
    const 给的 = app.stuckHints();
    assert.ok(给的.some((t) => 拆矛盾(app).includes(t)),
      '两轮之后她说过的话够拿来互相顶了');
  });
});

describe('不撞她刚顶回来的那句、也不重复我问过的（clash）', () => {
  test('她刚把「用途」两个字挡回来，就别再让玩家问用途', () => {
    const 给的 = fresh().stuckHints();
    assert.ok(!给的.some((t) => t.includes('用途') || t.includes('做什么用')),
      `她说的是「赎回还要说明用途」，这一排却把同一个问法原样推回去：${给的}`);
  });

  test('同一把钥匙换个问法接着给，不是整把消失', () => {
    const 给的 = fresh().stuckHints();
    assert.ok(给的.some((t) => t.includes('这笔钱')),
      '锚定用途这一把还在，只是不从"用途"两个字进去了');
  });

  test('我已经问过的话不再推第二遍', () => {
    const app = fresh({
      opening: '你谁啊。',
      turns: [
        一轮('您自己核实过对方是谁吗，怎么核实的？', '用不着你操心。'),
        一轮('那您想过没有？', '想过了。'),
      ],
    });
    assert.ok(!app.stuckHints().includes('您自己核实过对方是谁吗，怎么核实的？'),
      '这句他上上轮刚发过');
  });

  test('几轮前提过的说法不算撞——只看她刚回的那一句', () => {
    // clash 认的是"她刚设的那道挡箭牌"，不是"这一局出现过的所有词"。
    // 认整局的话，她随口提一次"用途"，锚定用途这一把从此就没了
    const app = fresh({
      opening: '怎么，赎回还要说明用途？',
      turns: [
        一轮('您别急，我就问两句。', '快点说。'),
        一轮('嗯。', '还有事吗。'),
        一轮('稍等。', '我很忙。'),
      ],
    });
    assert.ok(本地(app.stuckHints()).some((t) => t.includes('做什么用')),
      '她最近几轮都没再提用途，这个问法该回来了');
  });
});

describe('接得上她原话的优先（cue）', () => {
  test('她提了「群里老师」，拆矛盾就给能顶上去的那一句', () => {
    const app = fresh({
      turns: [
        一轮('这是谁跟您说的？', '群里老师带的，就那么几个名额。'),
        一轮('哦。', '你别管了。'),
      ],
    });
    assert.ok(app.stuckHints().includes('说是只给少数人的消息，怎么这么多人都在传？'),
      '她自己把「名额」和「群」摆在了一起，这一句是接着她说的');
  });

  test('她没提过的说法就不硬接', () => {
    const app = fresh({
      turns: [
        一轮('您别急。', '没什么好说的。'),
        一轮('嗯。', '挂了。'),
      ],
    });
    assert.ok(!app.stuckHints().includes('说是只给少数人的消息，怎么这么多人都在传？'),
      '她一个字没提过内部消息，这句话是无中生有');
  });
});

describe('档位只加权，不筛掉', () => {
  test('三把钥匙照样一起给，不按情绪档挑最强的那把', () => {
    // 按档位把最优的那把钥匙单推给玩家，就是每两轮发一次答案——
    // `keys.js` 那条"不按档位挑最优"说的正是这件事
    for (const mood of ['guarded', 'irritated', 'wavering', 'softening']) {
      const app = fresh({
        mood,
        turns: [
          一轮('您别急。', '嗯。'),
          一轮('那这钱呢？', '我自己的事。'),
        ],
      });
      const keys = app.stuckHints().map((t) =>
        app.STUCK_HINTS.find((h) => h.text === t).key);
      assert.equal(new Set(keys).size, 3, `${mood} 这一档少给了一把钥匙：${keys}`);
    }
  });
});

describe('这一排的形状', () => {
  test('三条互不相同', () => {
    const app = fresh({
      turns: [一轮('您别急。', '嗯。'), 一轮('那这钱呢？', '我自己的事。')],
    });
    const 给的 = app.stuckHints();
    assert.equal(new Set(给的).size, 给的.length, `重复了：${给的}`);
  });

  test('连着两轮不给一模一样的三句', () => {
    // 同一排原样再来一次，看着就像攻略在念稿
    const 两轮 = [一轮('您别急。', '嗯。'), 一轮('那这钱呢？', '我自己的事。')];
    const 第二轮 = fresh({ turns: 两轮 }).stuckHints();
    const 第三轮 = fresh({ turns: [...两轮, 一轮('那您打算怎么办？', '不用你管。')] })
      .stuckHints();
    assert.notDeepEqual(第二轮, 第三轮);
  });
});
