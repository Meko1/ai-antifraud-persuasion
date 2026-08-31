/* 生成参赛提交要用的图片素材。
 *
 *     node tools/capture_materials.mjs            # 落到 dist/materials/
 *
 * ## 为什么是脚本，不是手工截图
 *
 * 提交材料要重做的次数比想象中多（改一句文案、换一个结局、审核驳回重交）。
 * 手工截图每次都要重新走一遍十二轮、重新对齐、重新裁剪，而且**没人记得住
 * 上一次是在哪个尺寸下截的**。写成脚本之后，改完代码跑一次就全套重出。
 *
 * 复用 tests/e2e 那套东西（真 Chrome、真服务、零 npm 依赖），理由与那边
 * 一样，见 tests/e2e/cdp.mjs 顶部。
 *
 * ## 出什么
 *
 *   phone-*.png     手机版式原图（390×844 @2x），图集里直接能用
 *   card.png        分享卡原图，直接从 canvas 取，不经过截图
 *   cover-*.png     16:10 封面（1920×1200），大赛「封面图建议 16:10」那一条
 *
 * **封面是在浏览器里排出来的**，不是拿图片软件拼的：把手机原图当
 * `<img>` 塞进一页 HTML，排好版再截一次。这样版式改起来就是改 CSS，
 * 而且不用引任何图像库。
 *
 * ## 走的是离线演示态
 *
 * `OFFLINE_DEMO=true`（tests/e2e/server.mjs 就是这么起的）：不调网关、
 * 不花钱、每次跑出来的东西可比。**判分一格都不打折**，所以复盘那一屏上的
 * 数字全是真算出来的——但台词是罐头，这一点在素材里不影响，因为素材要展示
 * 的是版式与流程。
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  connect, evaluate, findChrome, launchChrome, newPage, waitFor,
} from '../tests/e2e/cdp.mjs';
import { startServer } from '../tests/e2e/server.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, '..', 'dist', 'materials');

const PHONE = { width: 390, height: 844, deviceScaleFactor: 2, mobile: true };
const COVER = { width: 1920, height: 1200, deviceScaleFactor: 1, mobile: false };

/** 十二轮说什么。
 *
 *  **每一句都对着 app/offline.py 的关键词表写**——离线态的分类是正则，
 *  不是模型。这里要的是"七把钥匙都被演到"，不是"打出最高分"：素材要展示
 *  的是这套判分在判什么，不是作者自己有多会玩。
 */
const 台词 = [
  '陈叔，这三十万本来是打算做什么用的？',
  '我听得出来您这两天挺着急的。',
  '您刚才说是内部消息，可之前您说群里谁都能进，这两句怎么放在一起？',
  '转不转由您决定，我不替您做主，只想请您先回答两个问题。',
  '王老师让您做的这几步，您能自己讲一遍给我听吗？',
  '正规机构从来不会这么做，这是个局。',
  '那笔钱原本是不是给女儿准备的？',
  '他为什么一定要今天三点前？',
  '您刚才说他保本，可他又说不承诺收益，这两句能同时成立吗？',
  '我知道您不容易，这些年攒下来不容易。',
  '这笔钱转过去之后，您打算怎么把它取回来？',
  '最后一件事由您定：今天先不转，明天我陪您一起核一遍，行吗？',
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function 存图(call, 文件名, 设备 = PHONE) {
  await call('Emulation.setDeviceMetricsOverride', 设备);
  await sleep(250); // 让重排与过渡动画落定，否则会截到半途的透明度
  const { data } = await call('Page.captureScreenshot', { format: 'png' });
  const 路径 = path.join(OUT, 文件名);
  fs.writeFileSync(路径, Buffer.from(data, 'base64'));
  const kb = Math.round(fs.statSync(路径).size / 1024);
  console.log(`  ✓ ${文件名}  ${kb} KB`);
  return data;
}

async function 打一轮(call, 说) {
  const 之前 = await evaluate(call, `document.querySelectorAll('#thread .msg.them').length`);
  await evaluate(call, `(() => {
    const el = document.getElementById('say');
    el.value = ${JSON.stringify(说)};
    el.dispatchEvent(new Event('input', { bubbles: true }));
    document.getElementById('composer').requestSubmit();
  })()`);
  // 结局可能提前到（被拉黑），那时候他不会再回话——所以两个条件都等
  await waitFor(call, `document.querySelectorAll('#thread .msg.them').length > ${之前}
    || !!document.querySelector('.review')`, '他回话', 30000);
  await waitFor(call, `!document.querySelector('#thread .bubble.typing')
    || !!document.querySelector('.review')`, '输入气泡收掉', 20000);
  await sleep(150);
}

/** 开局**之前**就把这一局钉在老陈那一场。
 *
 *  `钉住老陈()` 是补救——它只能在工作台那一屏用（「换一位客户」在那儿），
 *  而**转账确认屏与拦截屏在它之前**。于是 2026-08-30 之前出的素材里
 *  `phone-01` / `phone-02` 是随机某位客户，`phone-03` 起才是老陈：
 *  实测那一批的拦截屏印着「你是**她**的投资顾问」，紧挨着的下一张是老陈。
 *  图集里这两张是相邻的。
 *
 *  用的仍然是**界面自己的那把钥匙**：`aap.pick.sid` 正是「换一位客户」
 *  写下的那个键（`state.js` 的 `startNewClient`），这里只是提前到首屏之前写。
 *  **没有为出素材改任何产品行为**。 */
async function 开局前钉住老陈(call, base) {
  await call('Page.navigate', { url: `${base}/` });
  await waitFor(call, `document.readyState === 'complete'`, '首屏');
  // **打个记号再重载，然后等记号消失。** `Page.reload` 是发出去就返回的，
  // 不等新文档——直接往下走的话，后面那些 waitFor 全都命中的是**旧页面**
  // （旧页面上按钮当然是就绪的），于是重载会落在流程中间，把整段录废。
  // 实测第一版就是这样：录出来的片子里客户还是随机那位，章节也全错位了。
  await evaluate(call, `window.__钉 = 1`);
  // **存档也要清。** 首屏那一次 `loadGame()` 结尾就 `saveGame()` 了，
  // 于是重载时 `boot()` 认为"有存档"，直接续到聊天屏——录出来的片子里
  // 第 9 秒本该是拦截屏，实际是第 1 轮的对话，客户也还是随机那位。
  // 清存档 + 写 pick，正是界面自己的「换一位客户」做的两件事
  // （`state.js` 的 `startNewClient`：先 clearSaved，再写 PICK_KEY，再 reload）。
  await evaluate(call, `sessionStorage.removeItem('aap.game.v1');
    sessionStorage.removeItem('aap.transfer.seen');
    sessionStorage.setItem('aap.pick.sid', 'chen')`);
  await call('Page.reload');
  await waitFor(call, `!window.__钉 && document.readyState === 'complete'`, '重载后的新文档', 25000);
}

/** 把这一局钉在老陈那一场（工作台上那把补救钥匙）。
 *
 *  **场景默认是随机分配的**（POSITIONING「主张边界」最后一条），而上面那
 *  十二句台词里写着"陈叔""王老师"——不钉住，素材里就会出现拿着老陈的台词
 *  去劝周淑琴的画面。用的是界面自己的「换一位客户」，**没有为出素材改任何
 *  产品行为**；列表里找不到姓陈的，说明当前这一局本来就是他。
 *
 *  开局前那一手（`开局前钉住老陈`）生效时这里会直接返回——留着它是兜底：
 *  `aap.pick.sid` 万一读不到（隐私模式），这一步仍然把人换回来。 */
async function 钉住老陈(call) {
  if (await evaluate(call, `!!document.getElementById('pickClient')?.hidden`)) return;
  await evaluate(call, `document.getElementById('pickClient').click()`);
  await waitFor(call, `!document.getElementById('sheetHost').hidden`, '客户列表');
  const 换了 = await evaluate(call, `(() => {
    const it = [...document.querySelectorAll('.sheet-item')].find(b => /陈/.test(b.textContent));
    if (!it) return false;
    it.click();
    return true;
  })()`);
  if (!换了) {
    await evaluate(call, `document.querySelector('.sheet-cancel').click()`);
    return;
  }
  await waitFor(call, `document.getElementById('openingTitle')?.textContent.length > 0`,
    '换人之后重开的工作台', 25000);
  await sleep(500);
}

/** 封面那一页的 HTML。手机原图以 data URL 塞进去，排完版再截一次。 */
function 封面HTML({ 图, 眉, 标题, 说明, 标签 }) {
  return `<!doctype html><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  /* ── 弹幕带（就绪度审计 §1.3 · S4）─────────────────────────────────
     大赛展示页的**评论以弹幕形式飘在截图之上**。弹幕走的是画面上半部，
     所以这里把整块内容下压，让标题与副文落在下三分之二，
     顶部那一条留给弹幕当跑道——被盖住的是空背景，不是字。
     --danmu 就是那条跑道的高度，改版式时别把它吃掉。
     （这一段里不许出现反引号：整块 HTML 是模板串，反引号会把它截断。） */
  body {
    --danmu: 210px;
    width: ${COVER.width}px; height: ${COVER.height}px; overflow: hidden;
    display: flex; align-items: center; gap: 96px;
    padding: var(--danmu) 110px 0;
    background: radial-gradient(120% 120% at 12% 0%, #22252c 0%, #131417 58%, #0d0e11 100%);
    color: #fff;
    font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", -apple-system, sans-serif;
  }
  /* 跑道自己压深一点：浅色弹幕飘过深底才读得清，而这一条同时把
     "上面这一带是留白"变成一个看得出来的设计，不像截歪了 */
  body::before {
    content: ''; position: fixed; inset: 0 0 auto 0; height: var(--danmu);
    background: linear-gradient(180deg, rgba(0,0,0,.55) 0%, rgba(0,0,0,0) 100%);
  }
  .left { flex: 1 1 auto; min-width: 0; }
  .eyebrow {
    display: inline-flex; align-items: center; gap: 12px;
    font-size: 26px; letter-spacing: .22em; color: #c9a227; margin-bottom: 34px;
  }
  .eyebrow::before { content: ''; width: 46px; height: 2px; background: #c9a227; }
  h1 {
    font-size: 82px; line-height: 1.24; font-weight: 700; letter-spacing: -.01em;
    margin-bottom: 34px;
  }
  h1 em { font-style: normal; color: #c9a227; }
  p {
    font-size: 27px; line-height: 1.75; color: #a9adb6; max-width: 21em;
    margin-bottom: 46px;
  }
  .tags { display: flex; flex-wrap: wrap; gap: 14px; max-width: 24em; }
  .tags span {
    font-size: 21px; padding: 11px 20px; border-radius: 999px;
    border: 1px solid rgba(255,255,255,.16); color: #d6d9e0;
    background: rgba(255,255,255,.04);
  }
  .right { flex: 0 0 auto; position: relative; }
  .right img {
    width: 430px; display: block; border-radius: 38px;
    border: 1px solid rgba(255,255,255,.13);
    box-shadow: 0 60px 130px rgba(0,0,0,.62), 0 0 0 12px rgba(255,255,255,.03);
  }
  </style>
  <div class="left">
    <div class="eyebrow">${眉}</div>
    <h1>${标题}</h1>
    <p>${说明}</p>
    <div class="tags">${标签.map((t) => `<span>${t}</span>`).join('')}</div>
  </div>
  <div class="right"><img src="data:image/png;base64,${图}"></div>`;
}

async function 出封面(cdp, 文件名, 参数) {
  const { call } = await newPage(cdp);
  await call('Emulation.setDeviceMetricsOverride', COVER);
  const { frameTree } = await call('Page.getFrameTree');
  await call('Page.setDocumentContent', {
    frameId: frameTree.frame.id,
    html: 封面HTML(参数),
  });
  // 字体与那张 data URL 图都要真的解码完，否则会截到一半空的
  await waitFor(call, `document.fonts.ready.then(() => {
    const img = document.querySelector('img');
    return !!img && img.complete && img.naturalWidth > 0;
  })`, '封面里的图与字体', 20000);
  await sleep(300);
  const { data } = await call('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(OUT, 文件名), Buffer.from(data, 'base64'));
  console.log(`  ✓ ${文件名}  ${Math.round(fs.statSync(path.join(OUT, 文件名)).size / 1024)} KB`);
}

async function main() {
  const chromePath = findChrome();
  if (!chromePath) {
    console.error('找不到 Chrome。装一个，或用 CHROME_PATH 指过去。');
    process.exit(1);
  }
  fs.mkdirSync(OUT, { recursive: true });

  const server = await startServer();
  const chrome = await launchChrome(chromePath);
  const cdp = await connect(chrome.wsUrl);
  const { call } = await newPage(cdp);

  try {
    await call('Emulation.setDeviceMetricsOverride', PHONE);
    // 首屏之前就钉住，否则 phone-01/02 会是随机某位客户，而 phone-03 起是老陈
    await 开局前钉住老陈(call, server.base);
    await waitFor(call, `document.getElementById('transfer')?.classList.contains('on')`,
      '转账确认屏', 25000);
    await sleep(600);

    console.log('\n手机版式原图：');
    const 转账 = await 存图(call, 'phone-01-转账确认.png');

    await evaluate(call, `document.getElementById('transferGo').click()`);
    await waitFor(call, `!document.getElementById('transferHandoff').hidden`, '拦截那一面');
    await 存图(call, 'phone-02-被拦下.png');

    // **先等按钮解锁再点。** 「坐到对面」在换面之后被锁 450ms
    // （opening.js 的 `HANDOFF_ARM_MS`，防的是同一坐标连点两下把拦截面
    // 一帧不渲染地跳过去）。直接点会落在禁用态上，什么都不发生，
    // 然后卡在下一个 waitFor 上超时——**症状看着像页面坏了，其实是脚本手快**。
    await waitFor(call, `!document.getElementById('handoffGo').disabled`, '「坐到对面」解锁');
    await evaluate(call, `document.getElementById('handoffGo').click()`);
    await waitFor(call, `document.querySelector('.screen.on')?.id !== 'transfer'`, '离开转账屏', 25000);
    await waitFor(call, `document.getElementById('openingTitle')?.textContent.length > 0`,
      '工作台铺好', 25000);
    await 钉住老陈(call);
    await sleep(400);
    await 存图(call, 'phone-03-异动预警.png');

    await evaluate(call, `document.getElementById('openProfile').click()`);
    await waitFor(call, `document.getElementById('home')?.classList.contains('on')`, '客户档案');
    await sleep(300);
    await 存图(call, 'phone-04-客户档案.png');

    await evaluate(call, `document.getElementById('openChen').click()`);
    await waitFor(call, `document.getElementById('primer')?.classList.contains('on')`, '课程表');
    await sleep(300);
    // 文件名跟着屏上那句走（2026-08-30 从「七种问法」改成「七把钥匙」：
    // 七把里有四把不是问法，复盘本来就管它们叫钥匙）
    await 存图(call, 'phone-05-七把钥匙.png');

    await evaluate(call, `document.getElementById('primerGo').click()`);
    await waitFor(call, `document.getElementById('chat')?.classList.contains('on')`, '聊天屏');
    await waitFor(call, `document.querySelectorAll('#thread .msg').length >= 2`, '开场两条', 30000);
    await sleep(400);

    console.log('\n打一局（离线态，十二轮）：');
    let 对局图 = null;
    for (let i = 0; i < 台词.length; i += 1) {
      if (await evaluate(call, `!!document.querySelector('.review')`)) break;
      await 打一轮(call, 台词[i]);
      process.stdout.write(`  第 ${i + 1} 轮 `);
      // 第五轮之后对话已经有来有回，是最能代表这个作品的一帧——封面用它
      if (i === 4) {
        console.log('\n');
        对局图 = await 存图(call, 'phone-06-对局中.png');
      } else if (i === 台词.length - 1) {
        console.log('');
      }
    }

    // 结局不另起一块 UI，它就是这段对话里的最后一件东西（chat.js `finish`）：
    // 一张转账凭证 + 一个「看复盘」。这一帧值得单独留一张——
    // 它是"劝住/没劝住"这件事在屏幕上唯一的样子
    await waitFor(call, `!!document.querySelector('.endcta') || !!document.querySelector('.review')`,
      '结局那一帧', 40000);
    await sleep(500);
    console.log('\n结局、复盘与分享卡：');
    if (await evaluate(call, `!!document.querySelector('.endcta')`)) {
      await 存图(call, 'phone-07-结局.png');
      await evaluate(call, `document.querySelector('.endcta').click()`);
    }

    await waitFor(call, `!!document.querySelector('.review')`, '复盘页', 30000);
    await sleep(500);
    await 存图(call, 'phone-08-复盘首屏.png');

    // 时机对照那一块是全作品唯一竞品没有的判据，单独给它一张：
    // 它在第一屏底下，不滚过去截不到
    await evaluate(call, `document.getElementById('contrastWrap')?.scrollIntoView(
      { block: 'center', behavior: 'instant' })`);
    await sleep(400);
    await 存图(call, 'phone-09-同一句话换个时候说.png');

    // 「回到你自己那一笔」：冷开场那个环在这里合上，是全作品唯一
    // 把心理反思落回本人的一块，素材里不能少
    await evaluate(call, `document.getElementById('mirrorLead')?.closest('.group')
      ?.scrollIntoView({ block: 'center', behavior: 'instant' })`);
    await sleep(400);
    await 存图(call, 'phone-10-回到你自己那一笔.png');

    await evaluate(call, `(() => {
      const d = document.getElementById('reviewDetails');
      d.open = true; d.dispatchEvent(new Event('toggle'));
    })()`);
    await waitFor(call, `document.getElementById('chart').width > 200`, 'K 线铺开');
    await evaluate(call, `document.getElementById('makeCard').click()`);
    await waitFor(call, `document.getElementById('card')?.width > 0`, '分享卡');
    await sleep(400);

    // 分享卡直接从 canvas 取，不走截图——截图会带上页面背景和缩放，
    // 而这张图本来就是要被单独传出去的
    const 卡 = await evaluate(call,
      `document.getElementById('card').toDataURL('image/png').split(',')[1]`);
    fs.writeFileSync(path.join(OUT, 'card-分享卡.png'), Buffer.from(卡, 'base64'));
    console.log(`  ✓ card-分享卡.png  ${
      Math.round(fs.statSync(path.join(OUT, 'card-分享卡.png')).size / 1024)} KB`);

    console.log('\n16:10 封面：');
    await 出封面(cdp, 'cover-01-对局中.png', {
      图: 对局图 || 转账,
      眉: 'AI 反诈劝阻',
      标题: '他正要按下确认。<br>你有三分钟，<em>去劝另一个他。</em>',
      说明: '风险提示要人先承认自己被骗——正在转账的人恰恰最不肯承认。'
        + '角色对调不要求他承认任何事：他只需要去劝一个和他处境一模一样的人。',
      标签: ['5 个诈骗场景', '24 个人格变体', '12 轮对局', '判分由程序算，不由模型打'],
    });
    await 出封面(cdp, 'cover-02-异动触发.png', {
      图: 转账,
      眉: '它什么时候弹出来',
      标题: '清仓、大额转出——<br><em>按下确认之前的最后一帧。</em>',
      说明: '证券资金只能在本人同名账户之间实时划转。这一帧是券商能看见的最后一帧，'
        + '下一秒钱去哪儿账户上再也看不到——所以干预只能发生在按下之前。',
      标签: ['账户异动触发', '不打断交易主链路', '三分钟', '对照组可比'],
    });
  } finally {
    cdp.close();
    chrome.kill();
    server.stop();
  }

  console.log(`\n全部落在 ${path.relative(process.cwd(), OUT)}/`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
