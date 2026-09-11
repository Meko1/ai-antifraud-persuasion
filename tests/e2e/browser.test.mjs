/* 真浏览器端到端（复核清单 P2-2）。
 *
 * ## 它和另外那 86 条的分工
 *
 * 那 86 条跑在 `node:vm` 里，DOM 是个"问什么都答应"的替身——**渲染结果
 * 一个字都验不了**，这一点写在 harness.mjs 顶部。于是有两类问题它们天生
 * 看不见，而这两类恰好都是"路演当天才发现"的那种：
 *
 * 1. **模块图连不起来。** 单测把全部模块拼成一份脚本再跑，漏写一句
 *    `import` 照样绿（modules.test.mjs 静态查了一半，这里是另一半：
 *    真浏览器、真 `<script type="module">`、真的一个个去拉）。
 * 2. **画出来是什么样。** 复盘第一屏那条"五块是硬上限"此前只能读源码里的
 *    模板去数——**数的是写了几块，不是长出来几块**。一个 `hidden` 忘了摘、
 *    一个 group 被塞进别处，源码那条数法一概看不出来。这里数的是
 *    `document` 上真实存在的节点。
 *
 * ## 为什么不用 Playwright
 *
 * 不许引 npm 依赖（harness.mjs 顶部第 1 条）。CDP 只要 HTTP + WebSocket，
 * Node 22 起 WebSocket 是内建的——手写一百来行比一份 lockfile 便宜。
 * 客户端在 cdp.mjs，服务在 server.mjs。
 *
 * 没有 Chrome 就整组跳过，并且**把原因印出来**：一组悄悄跳过的测试
 * 和没有测试是一回事。
 */

import assert from 'node:assert/strict';
import { after, before, describe, test } from 'node:test';

import {
  connect, evaluate, findChrome, launchChrome, newPage, waitFor, waitUntil,
} from './cdp.mjs';
import { MODULES } from '../frontend/harness.mjs';
import { startServer } from './server.mjs';

const chromePath = findChrome();
const 跳过 = chromePath
  ? false
  : '找不到 Chrome。装一个，或用 CHROME_PATH 指过去。';

describe('真浏览器：整条路走一遍', { skip: 跳过 }, () => {
  let server;
  let chrome;
  let cdp;
  let call;
  /** 控制台里的报错与页面异常。**任何一条都算失败**——白屏就是从这儿开始的。 */
  const 报错 = [];
  /** 每个静态资源的状态码，用来验模块图真的一个个拉下来了。 */
  const 响应 = new Map();

  before(async () => {
    server = await startServer();
    chrome = await launchChrome(chromePath);
    cdp = await connect(chrome.wsUrl);
    ({ call } = await newPage(cdp));

    cdp.on('Runtime.consoleAPICalled', (p) => {
      if (p.type === 'error') {
        报错.push(p.args.map((a) => a.value ?? a.description ?? '').join(' '));
      }
    });
    cdp.on('Runtime.exceptionThrown', (p) => {
      报错.push(p.exceptionDetails?.exception?.description
        || p.exceptionDetails?.text || '未知异常');
    });
    cdp.on('Log.entryAdded', (p) => {
      // CSP 拦下来的东西走这里，不走 console——**这一条不能漏**：
      // 真出了 CSP 问题，页面是静悄悄地白，控制台里什么都没有。
      //
      // favicon 除外：那是浏览器自己去要的，这个作品从来没有过一个
      // favicon，也不该为了让一条测试好看就去加一个
      if (p.entry.level === 'error' && !/favicon\.ico/.test(p.entry.url || '')) {
        报错.push(`[${p.entry.source}] ${p.entry.text}`);
      }
    });
    cdp.on('Network.responseReceived', (p) => {
      响应.set(p.response.url.replace(server.base, ''), p.response.status);
    });
    cdp.on('Network.loadingFailed', (p) => {
      报错.push(`请求失败：${p.errorText}（${p.type}）`);
    });

    await call('Page.navigate', { url: `${server.base}/` });

    /* 等的是**响应本身**，不是页面上某个节点。
       踩过两次的坑：`#openChen` 和 `.screen.on` 都是 index.html 里的静态
       东西，HTML 一解析出来就有，模块一个都还没拉——等它们等于没等，
       下面「模块一个不落」那条于是在响应还没到的时候就去数。
       这里直接等那十三条响应，超时了就说清是哪几个没到。 */
    await waitUntil(() => MODULES.every((m) => 响应.has(`/static/${m}`)), {
      what: () => `模块加载（还差 ${MODULES.filter((m) => !响应.has(`/static/${m}`))}）`,
      timeout: 20000,
    });
    // 再等开局那一路走完，后面每一条都建立在"这一局真的开起来了"之上
    await waitFor(call,
      `document.querySelector('.screen.on')?.id !== 'assignment'`, '开局完成', 25000);

    /* 塞一局假的历史进去。**为的是把「本机对局记录」那一块画出来**——
       它只在"这台设备之前打过"时才显示（history.js 的 `prior.length`），
       而一次干净的 E2E 永远没有历史，于是那一整块从来没被浏览器画过一次。
       它里面的「最少历练」卡就是 8-31 那个 `{ta}` 裸奔 bug 的案发地。
       字段只需凑够 paintHistory 读的那几个；`uses` 全零，让"最少历练"
       一定挑得出一把钥匙来。 */
    await evaluate(call, `localStorage.setItem('af_history_v1', JSON.stringify([{
      ts: Date.now() - 864e5, sid: 'chen', clientName: '陈国栋',
      kind: 'stalled', trust: 42, rounds: 10, uses: {}, gains: {},
    }]))`);
  });

  after(async () => {
    cdp?.close();
    chrome?.kill();
    server?.stop();
  });

  test('模块图里每一个，浏览器都一个不落地拉了下来', async () => {
    for (const m of MODULES) {
      assert.equal(响应.get(`/static/${m}`), 200,
        `/static/${m} 没被拉下来（拿到 ${响应.get(`/static/${m}`)}）——`
        + '要么谁的 import 写错了路径，要么它压根没进模块图');
    }
  });

  test('CSP 还是那条，模块脚本在它底下照样跑得起来', async () => {
    const csp = await evaluate(call,
      `fetch(location.href).then(r => r.headers.get('content-security-policy'))`);
    assert.match(csp, /script-src 'self'/,
      '响应头里的 script-src 变了 —— 换 ES module 本来就不该动它');
    assert.doesNotMatch(csp, /unsafe-inline/,
      'script-src 放宽到 unsafe-inline 了。同源相对 import 不需要这个');
    // 脚本真跑起来了才会有事件监听器；上面 before 里那个 waitFor 已经证明了
    // 这一点，这里再钉一下"不是被 CSP 挡下之后碰巧有个静态节点"
    assert.equal(await evaluate(call, `document.querySelectorAll('.screen').length > 0`), true);
  });

  test('工作台那一屏的内容来自服务端下发的场景，不是写死的', async () => {
    await waitFor(call, `document.querySelector('#openingTitle')?.textContent.length > 0`,
      '开场屏铺好');
    const 屏 = await evaluate(call, `(() => {
      const $ = (id) => document.getElementById(id);
      return {
        title: $('openingTitle').textContent,
        money: $('openingMoney').textContent,
        client: $('cName').textContent,
        cta: $('ctaLabel').textContent,
        今日: $('todayCount').textContent,
      };
    })()`);
    assert.ok(屏.title.length > 4, `开场标题是空的：${JSON.stringify(屏)}`);
    assert.match(屏.money, /^¥[\d,]+$/, `金额没铺上：${屏.money}`);
    assert.ok(屏.client.length >= 2, `客户名没铺上：${屏.client}`);
    assert.match(屏.cta, /发消息$/);
    assert.match(屏.今日, /今日第 \d+ 位客户/);
  });

  test('开打前先过一遍课程表，而且只给 brief 不给 tip', async () => {
    await evaluate(call, `document.getElementById('openChen').click()`);
    await waitFor(call, `document.getElementById('primer')?.classList.contains('on')`,
      '课程表那一屏');
    const 条目 = await evaluate(call,
      `[...document.querySelectorAll('#primerList li')].map(li => li.textContent)`);
    assert.equal(条目.length, 7, '七把钥匙一把都不能少');
    for (const t of 条目) {
      assert.doesNotMatch(t, /戒备|烦躁|松动|倍|\d+\s*分/,
        `课程表上泄了时机或分值：${t}`);
    }
  });

  test('进聊天：你先发的那一条在，他的开场白是在回它', async () => {
    await evaluate(call, `document.getElementById('primerGo').click()`);
    await waitFor(call, `document.getElementById('chat')?.classList.contains('on')`, '聊天屏');
    await waitFor(call, `document.querySelectorAll('#thread .msg').length >= 2`, '开场两条');
    const 开场 = await evaluate(call, `[...document.querySelectorAll('#thread .msg')].map(
      m => [m.classList.contains('me') ? 'me' : 'them', m.querySelector('.bubble').textContent])`);
    assert.equal(开场[0][0], 'me', '第一条得是你发的那句「账户动了」');
    assert.equal(开场[1][0], 'them');
    assert.ok(开场[1][1].length > 4, '他的开场白是空的');
  });

  test('空输入时发送键是灰的，敲了字才亮', async () => {
    assert.equal(await evaluate(call, `document.getElementById('send').disabled`), true,
      '一个按下去什么都不发生的按钮，比一个明确禁用的按钮更难懂');
    await evaluate(call, `(() => {
      const el = document.getElementById('say');
      el.value = '这笔钱本来是打算做什么用的？';
      el.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    assert.equal(await evaluate(call, `document.getElementById('send').disabled`), false);
  });

  test('打一轮：SSE 逐句到，判分记在这一局上', async () => {
    const 之前 = await evaluate(call, `({
      them: document.querySelectorAll('#thread .msg.them').length,
      remaining: Number(document.getElementById('remaining').textContent),
    })`);
    await evaluate(call, `document.getElementById('composer').requestSubmit()`);
    await waitFor(call,
      `document.querySelectorAll('#thread .msg.them').length > ${之前.them}`, '他回话', 30000);
    // 「正在输入」那个气泡最后要收掉，否则他看起来永远在打字
    await waitFor(call, `!document.querySelector('#thread .bubble.typing')`, '输入气泡收掉');

    // 剩余轮次要真的少一格。**不看 turnCurrent**：它显示的是"刚打完的是第几轮"
    // （playTurn 里 `meta` 事件那一行），打完第 1 轮它仍然是 1，不是 2
    const 之后 = await evaluate(call, `({
      remaining: Number(document.getElementById('remaining').textContent),
      当前轮: document.getElementById('turnCurrent').textContent,
      出错: !!document.querySelector('#thread .sysnote.bad'),
      判分卡: document.querySelectorAll('#thread .sysnote').length,
    })`);
    assert.equal(之后.出错, false, '这一轮报错了，聊天窗口里挂着一条红提示');
    assert.equal(之后.remaining, 之前.remaining - 1,
      `剩余轮次没往下走：${之前.remaining} → ${之后.remaining}`);
    /* 抬头那个数说的是**下一轮**，不是刚打完那一轮。打完第 1 轮之后
       他正在想第 2 句，那个数就该是 2——旁边的格子这时候是 11，
       「第 1 轮 / 10」配「剩 9」是同一行自相矛盾（chat.js 里那段注释）。 */
    assert.equal(之后.当前轮, '2',
      '打完第 1 轮之后抬头没往前走 —— 玩家看这个数是为了知道还能说几次');
    // 判分卡不在对局中出现——边打边给答案等于把攻略印在屏幕上。
    // 这一句在沙箱里验不了：那边 `innerHTML` 不解析成节点
    assert.equal(之后.判分卡, 0, '对局中冒出了判分卡，标签与分数一律该留到复盘');

    // 抬头那颗「看复盘」第 4 轮才该出现（EARLY_REVIEW_FROM），这里才打完
    // 第 1 轮——早出现等于在人还没打进去的时候先递一个出口
    assert.equal(await evaluate(call, `document.getElementById('earlyReview').hidden`), true,
      '第 4 轮之前就露出来了');
  });

  test('退出那一层：三条出路，不设挽留', async () => {
    await evaluate(call, `document.getElementById('chatExit').click()`);
    await waitFor(call, `!document.getElementById('sheetHost').hidden`, '退出面板');
    const 出路 = await evaluate(call,
      `[...document.querySelectorAll('.sheet-item')].map(b => b.querySelector('b').textContent)`);
    assert.equal(出路.length, 3, `三条出路一条都不能少：${JSON.stringify(出路)}`);
    assert.match(出路[0], /稍后继续/);
    assert.match(出路[1], /就到这儿/);
    assert.match(出路[2], /直接离开/);
    // 焦点要落进面板里，不能还留在背景那一屏的输入框上
    assert.equal(await evaluate(call,
      `document.activeElement.closest('.sheet') !== null`), true,
      '焦点没进面板 —— 键盘用户会被困在看不见的背景里');
  });

  test('主动结束之后不编造资金结局', async () => {
    await evaluate(call, `[...document.querySelectorAll('.sheet-item')][1].click()`);
    await waitFor(call, `!!document.querySelector('.review')`, '复盘页');
    const 卡 = await evaluate(call, `(() => {
      const v = document.querySelector('.review');
      return {
        tone: v.dataset.tone,
        tier: v.querySelector('.tierpill').textContent,
        amt: v.querySelector('.savedamt').textContent,
        basis: v.querySelector('#resultBasis').textContent,
      };
    })()`);
    assert.equal(卡.amt, '未产生结果',
      `主动结束那一档给出了金额：${卡.amt} —— 那是凭空捏造的结果`);
    assert.equal(卡.tone, 'plain', '主动退出不是失败，不给红');
    assert.match(卡.basis, /没有资金状态/);
  });

  test('复盘第一屏：真的只长出五块', async () => {
    /* **这一条是这组测试存在的头号理由。** 同名的那一条（review.test.mjs
       末尾）数的是源码模板里写了几块；这里数的是浏览器里真实存在、
       而且没被 hidden 收掉的那几块。两者会在不同的地方失败：
       模板那条挡不住"忘了摘 hidden"，这条挡不住"写进去但这一局刚好没显示"。 */
    const 块 = await evaluate(call, `[...document.querySelectorAll(
      '.review-body > section.summary, .review-body > .group')]
      .filter(el => !el.hidden)
      .map(el => el.querySelector('.group-title')?.textContent ?? '结算卡')`);
    assert.ok(块.length <= 5,
      `第一屏长出了 ${块.length} 块，上限 5（PIVOT-C-END §3.2）：${JSON.stringify(块)}`);
    assert.ok(块.includes('结算卡'), '结算卡不见了');
  });

  test('砍掉的那几块收在折叠里，不是删了', async () => {
    for (const id of ['keyBars', 'roundsList', 'chart']) {
      assert.equal(await evaluate(call, `!!document.getElementById('${id}')`), true,
        `#${id} 在页面上不存在了——那不是折叠，是删除`);
      assert.equal(await evaluate(call,
        `document.getElementById('${id}').closest('.evidence-content') !== null`), true,
        `#${id} 没在「详细复盘」折叠里`);
    }
  });

  test('K 线要等折叠展开、量到真宽度才画', async () => {
    // 这里曾经量到 16px 就照着画，K 线糊成一片，而且是**间歇性**的
    await evaluate(call, `document.getElementById('reviewDetails').open = true;
      document.getElementById('reviewDetails').dispatchEvent(new Event('toggle'))`);
    await waitFor(call, `document.getElementById('chart').width > 200`, 'K 线按真宽度铺开');
  });

  test('整页没有一个 {ta} 裸奔到屏幕上', async () => {
    /* **这一条是 pronoun.test.mjs 的另一半。** 那份测试问的是"源码里有没有
       写死「他」"，单向；它绿着的时候，`history.js` 仍然把
       `KEYS[weakest].tip` 直接赋给 textContent，于是复盘「详细复盘 →
       关键方法」那张卡上原样印着：

           被洗脑的人满脑子是收益率。让{ta}自己说出「这笔钱本来是…

       写死代词是印错人，占位符裸奔是**把一段代码印给用户看**。
       静态那边补了一条（从词表取文案必须过 withTa），但真正说了算的是
       渲染结果——所以这里直接问浏览器。跑在折叠展开之后，那时候
       逐轮表、能力画像、本机记录三块都已经画出来了。

       **只扫看得见的文本。** index.html 里躺着几处带 `{ta}` 的静态兜底
       （`#handoffRole`、`#primerFoot`），它们由各自的 paint 在那一屏显示
       **之前**换掉——`#handoffRole` 归 `confirmTransfer()`，而这条 E2E 的
       路线压根没点过「确认转出」，那一屏从头到尾 `hidden`。把隐藏节点也算
       进来，这条测试报的就是"未水合的模板"，不是"用户看见的东西"。
       第一版就是这么误报的。

       "显示之前有没有换掉"是另一个判据，由 `tests/frontend/pronoun.test.mjs`
       的「开打前那一屏」那一组按时序单独钉（那才是慢网竞态的案发地）。 */
    const 裸奔 = await evaluate(call, `(() => {
      const out = [];
      const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n = walk.nextNode(); n; n = walk.nextNode()) {
        if (!n.nodeValue.includes('{ta}')) continue;
        const el = n.parentElement;
        // 看不见的不算：getClientRects 为空 = 这一屏根本没在显示
        if (!el || !el.getClientRects().length) continue;
        out.push((el.className || '?') + ' :: ' + n.nodeValue.trim().slice(0, 60));
      }
      return out;
    })()`);
    assert.deepEqual(裸奔, [],
      `{ta} 原样印在屏幕上了，渲染方漏了 withTa()：\n${裸奔.join('\n')}`);
  });

  test('本机对局记录那一块真的画出来了', async () => {
    // 上面那条 `{ta}` 断言的价值全押在"案发地真被画了"上。这一条钉住它：
    // 哪天 before 里那份种子失效，`{ta}` 那条会变成一条永远绿的空跑
    assert.equal(await evaluate(call,
      `!document.getElementById('historyWrap').hidden`), true,
      'historyWrap 还是 hidden —— 种进去的那局历史没生效');
    assert.equal(await evaluate(call,
      `!document.getElementById('historyWeak').hidden`), true,
      '「最少历练」那张卡没画出来 —— {ta} 那条断言就落空了');
  });

  test('排行那一行没东西说的时候，屏幕上一个字都没有', async () => {
    /* 2026-09-11。在此之前那一行写死着一句占位文案，而三条路——Redis 没配、
       连不上、样本没攒够——都不会回来改它，于是它是一句常驻文案。
       这一趟正好把三条路走全了：E2E 不连 Redis，而这一局是**主动结束**
       （上面那条「不编造资金结局」），不入档，本机排名那一级也不该开口。
       两级都没有 = 整行不出现，不是显示一句"暂不显示"。 */
    const 行 = await evaluate(call, `(() => {
      const el = document.getElementById('percentileLine');
      return { 有: !!el, 藏着: el?.hidden, 字: (el?.textContent || '').trim() };
    })()`);
    assert.equal(行.有, true, '那一行的节点本身得在，是 hidden 不是删了');
    assert.equal(行.藏着, true, '没数据就不出现（static/stats.js 顶上那条原则）');
    assert.equal(行.字, '', '不许再留占位文案');
  });

  test('分享卡画得出来，而且是张真图', async () => {
    await evaluate(call, `document.getElementById('makeCard').click()`);
    await waitFor(call, `document.getElementById('card')?.width > 0`, '分享卡');
    const 卡 = await evaluate(call, `(() => {
      const c = document.getElementById('card');
      return { w: c.width, h: c.height, 空白: c.toDataURL().length < 5000 };
    })()`);
    assert.ok(卡.w >= 640 && 卡.h > 400, `分享卡尺寸不对：${JSON.stringify(卡)}`);
    assert.equal(卡.空白, false, '分享卡是空白的');
  });

  test('全程一条控制台报错都没有', () => {
    /* 放在最后跑：上面每一步都可能留下报错，这里一起清算。
       **模块图出问题的典型症状就是这里冒出一条** ——
       `Failed to resolve module specifier` 或者 `does not provide an export named`。 */
    assert.deepEqual(报错, [], `页面上有报错：\n${报错.join('\n')}`);
  });
});

if (跳过) console.error(`\n⚠️  真浏览器 E2E 整组跳过了：${跳过}\n`);
