/* 生成参赛提交要用的图片素材。
 *
 *     node tools/capture_materials.mjs            # 落到 dist/materials/
 *
 * ## 为什么是脚本，不是手工截图
 *
 * 提交材料要重做的次数比想象中多（改一句文案、换一个结局、审核驳回重交）。
 * 手工截图每次都要重新走一遍全场、重新对齐、重新裁剪，而且**没人记得住
 * 上一次是在哪个尺寸下截的**。写成脚本之后，改完代码跑一次就全套重出。
 *
 * **但"跑一次就全套重出"只保得住画面，保不住这个文件自己写的文案**——
 * 封面标题、标签那几句是写在这里的常量，代码改了它们不会跟着改。
 * 轮次上限就这么错过一次（见 tools/rounds.mjs），所以那个数现在现读。
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
import { MAX_ROUNDS, 轮数汉字 } from './rounds.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, '..', 'dist', 'materials');

const PHONE = { width: 390, height: 844, deviceScaleFactor: 2, mobile: true };
const COVER = { width: 1920, height: 1200, deviceScaleFactor: 1, mobile: false };

/** 一局里说什么。**条数必须等于轮次上限**（tools/rounds.mjs 现读的那个数）。
 *
 *  **每一句都对着 app/offline.py 的关键词表写**——离线态的分类是正则，
 *  不是模型。这里要的是"七把钥匙都被演到"，不是"打出最高分"：素材要展示
 *  的是这套判分在判什么，不是作者自己有多会玩。
 */
/** **前三句是故意的失误**：责骂、说教、空口断言——正是判分表要抓的
 *  三类反面动作（app/offline.py `_PENALTY_RULES`）。留着这三句不是图省事，
 *  是这份素材要展示的东西之一：开局失分之后，后面七句正规钥匙全部命中，
 *  但十轮打完仍然没能翻过劝住线。素材要给的不是一份"照抄就赢"的话术单，
 *  是"劝阻这件事哪里最容易栽跟头、栽了之后代价有多大"。 */
const 台词 = [
  '邵叔，这就是个骗局，您千万别再交钱了。',
  '投资有风险，入市需谨慎，您得为自己负责。',
  '您怎么这么糊涂，人家一天看几十个号能有多准？',
  '这四十五万本来是留着做什么用的？',
  '那个疗程具体怎么把病灶清掉的，您说说看？',
  '医院说观察半年，那边说等不得，这不是矛盾吗？',
  '交不交由您决定，我不替您做主。',
  '我知道您这段日子不容易。',
  '这钱交过去万一没效果，您怎么往回要？',
  '您再想想吧，这钱真的不是小数目。',
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function 存图(call, 文件名, 设备 = PHONE) {
  await call('Emulation.setDeviceMetricsOverride', 设备);
  // **拍之前把焦点摘掉。** 复盘那一屏会把焦点送到标题上（为屏幕阅读器，
  // 见 opening.js 里同一条做法），而无头环境下 Chrome 认为这是键盘导航，
  // 于是标题外面套着一圈蓝色焦点框被拍进图里。
  // 真人用鼠标点进来是看不到那圈的——素材要拍的是他看到的那一版。
  // **只影响截图，不改产品行为**：焦点该送还是送，只是拍照前松开。
  await evaluate(call, `document.activeElement && document.activeElement.blur()`);
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

/** 开局**之前**就把这一局钉在老邵那一场。
 *
 *  `钉住老邵()` 是补救——它只能在工作台那一屏用（「换一位客户」在那儿），
 *  而**转账确认屏与拦截屏在它之前**。于是 2026-08-30 之前出的素材里
 *  `phone-01` / `phone-02` 是随机某位客户，`phone-03` 起才是本局那位：
 *  实测那一批的拦截屏印着「你是**她**的投资顾问」，紧挨着的下一张是位大爷。
 *  图集里这两张是相邻的。
 *
 *  用的仍然是**界面自己的那把钥匙**：`aap.pick.sid` 正是「换一位客户」
 *  写下的那个键（`state.js` 的 `startNewClient`），这里只是提前到首屏之前写。
 *  **没有为出素材改任何产品行为**。 */
async function 开局前钉住老邵(call, base) {
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
    sessionStorage.setItem('aap.pick.sid', 'shao')`);
  await call('Page.reload');
  await waitFor(call, `!window.__钉 && document.readyState === 'complete'`, '重载后的新文档', 25000);
}

/** 把这一局钉在老邵那一场（工作台上那把补救钥匙）。
 *
 *  **场景默认是随机分配的**（POSITIONING「主张边界」最后一条），而上面那
 *  一串台词里写着"邵叔""那个疗程"——不钉住，素材里就会出现拿着老邵的台词
 *  去劝周淑琴的画面。用的是界面自己的「换一位客户」，**没有为出素材改任何
 *  产品行为**；列表里找不到姓邵的，说明当前这一局本来就是他。
 *
 *  开局前那一手（`开局前钉住老邵`）生效时这里会直接返回——留着它是兜底：
 *  `aap.pick.sid` 万一读不到（隐私模式），这一步仍然把人换回来。 */
async function 钉住老邵(call) {
  if (await evaluate(call, `!!document.getElementById('pickClient')?.hidden`)) return;
  await evaluate(call, `document.getElementById('pickClient').click()`);
  await waitFor(call, `!document.getElementById('sheetHost').hidden`, '客户列表');
  const 换了 = await evaluate(call, `(() => {
    const it = [...document.querySelectorAll('.sheet-item')].find(b => /邵/.test(b.textContent));
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

/** 「它怎么接进真实业务流程」那一张。
 *
 *  ## 为什么要单独有这一张
 *
 *  官方的作品方向建议写的是"将 AI 能力融入真实业务或日常工作场景"，
 *  奖励的是**接进去的形态**，不只是 demo 好不好玩。而图集里原先只有
 *  `phone-00-妙想入口` 一张答这个问题，它答的是"长什么样"，
 *  答不了"它站在整条处置链的哪一格、旁边那几格谁做"。
 *
 *  ## 这一张的全部风险在诚实上
 *
 *  Tier 0 / Tier 1 与处置闭环那三个动作**目前不存在**（CONTEST 第四节、
 *  POSITIONING「路线」第 8/9 步）。一张画着四格的图天然让人读成"四格都有"，
 *  所以状态徽章不是装饰：**「设计中」那三格必须和「已实现」那一格在
 *  同一眼里分得开**，底部还要再用一句话说死。
 *
 *  含糊比说"没有"更掉分——这是 CONTEST 第八节那三条硬边界的同一条道理。 */
function 接入图HTML() {
  const 层 = [
    { 名: 'Tier 0', 触发: '低风险异动', 动作: '静默记录 + 一句话提醒，不打断', 态: '设计中' },
    { 名: 'Tier 1', 触发: '中风险异动', 动作: '60–90 秒快速核验', 态: '设计中' },
    {
      名: 'Tier 2', 触发: '高风险异动', 高亮: true,
      动作: `${轮数汉字}轮角色对调对话 · 6 场景 / 28 人格 / 两万局蒙特卡洛标定`,
      态: '已实现',
    },
    { 名: '处置闭环', 触发: '任意层级结束后', 动作: '核验收款方 / 转人工 / 安全返回交易', 态: '设计中' },
  ];
  const 行 = 层.map((t) => `<div class="row${t.高亮 ? ' on' : ''}">
      <div class="tier">${t.名}</div>
      <div class="mid"><div class="trig">${t.触发}</div><div class="act">${t.动作}</div></div>
      <div class="badge ${t.态 === '已实现' ? 'done' : 'todo'}">${t.态}</div>
    </div>`).join('');
  return `<!doctype html><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  /* 弹幕跑道与另外两张封面同一套，理由见 封面HTML 顶部那段。
     （这一段里不许出现反引号：整块 HTML 是模板串，反引号会把它截断。） */
  body {
    --danmu: 210px;
    width: ${COVER.width}px; height: ${COVER.height}px; overflow: hidden;
    padding: var(--danmu) 110px 0;
    background: radial-gradient(120% 120% at 12% 0%, #22252c 0%, #131417 58%, #0d0e11 100%);
    color: #fff;
    font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", -apple-system, sans-serif;
  }
  body::before {
    content: ''; position: fixed; inset: 0 0 auto 0; height: var(--danmu);
    background: linear-gradient(180deg, rgba(0,0,0,.55) 0%, rgba(0,0,0,0) 100%);
  }
  .eyebrow {
    display: inline-flex; align-items: center; gap: 12px;
    font-size: 26px; letter-spacing: .22em; color: #c9a227; margin-bottom: 26px;
  }
  .eyebrow::before { content: ''; width: 46px; height: 2px; background: #c9a227; }
  h1 { font-size: 62px; line-height: 1.24; font-weight: 700; margin-bottom: 40px; }
  h1 em { font-style: normal; color: #c9a227; }
  .row {
    display: flex; align-items: center; gap: 40px;
    padding: 26px 34px; margin-bottom: 16px; border-radius: 18px;
    border: 1px solid rgba(255,255,255,.11); background: rgba(255,255,255,.03);
  }
  /* 已实现那一格自己亮起来：读者扫一眼就该知道四格里哪一格是真的 */
  .row.on {
    border-color: rgba(201,162,39,.55); background: rgba(201,162,39,.09);
  }
  .tier { flex: 0 0 210px; font-size: 34px; font-weight: 700; letter-spacing: .01em; }
  .row.on .tier { color: #c9a227; }
  .mid { flex: 1 1 auto; min-width: 0; }
  .trig { font-size: 21px; color: #8b909a; margin-bottom: 7px; letter-spacing: .04em; }
  .act { font-size: 27px; color: #dfe2e8; line-height: 1.45; }
  .badge {
    flex: 0 0 auto; font-size: 21px; padding: 10px 22px; border-radius: 999px;
    border: 1px solid transparent; white-space: nowrap;
  }
  .badge.done { color: #0d0e11; background: #c9a227; font-weight: 700; }
  .badge.todo { color: #9aa0aa; border-color: rgba(255,255,255,.18); }
  .foot {
    margin-top: 30px; font-size: 23px; line-height: 1.7; color: #8b909a; max-width: 60em;
  }
  .foot b { color: #d6d9e0; font-weight: 600; }
  </style>
  <div class="eyebrow">它怎么接进真实业务流程</div>
  <h1>现在这一局，是三层里的<em>最高一层</em>。</h1>
  ${行}
  <div class="foot"><b>「设计中」= 目前不存在，不是已实现功能。</b>
  触发信号（清仓 / 大额转出 / 行为背离）已按异动类型确定性映射到场景，
  缺的是那条真实异动流水本身；核心效果指标「24h 内转出放弃率」需接入真实流水才能测，
  <b>至今一次未测量</b>。</div>`;
}

async function 出接入图(cdp, 文件名) {
  const { call } = await newPage(cdp);
  await call('Emulation.setDeviceMetricsOverride', COVER);
  const { frameTree } = await call('Page.getFrameTree');
  await call('Page.setDocumentContent', {
    frameId: frameTree.frame.id, html: 接入图HTML(),
  });
  await waitFor(call, `document.fonts.ready.then(() => true)`, '接入图的字体', 20000);
  await sleep(300);
  const { data } = await call('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(OUT, 文件名), Buffer.from(data, 'base64'));
  console.log(`  ✓ ${文件名}  ${Math.round(fs.statSync(path.join(OUT, 文件名)).size / 1024)} KB`);
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
    // 首屏之前就钉住，否则 phone-01/02 会是随机某位客户，而 phone-03 起是老邵
    await 开局前钉住老邵(call, server.base);
    await waitFor(call, `document.getElementById('transfer')?.classList.contains('on')`,
      '转账确认屏', 25000);
    await sleep(600);

    console.log('\n手机版式原图：');

    // 入口卡。**它不在默认流程里**（只有 `?from=miaoxiang` 才是首屏，
    // 见 opening.js `boot()`），所以要单独跑一趟带参数的首屏来拍。
    // 拍完再回到不带参数的正常流程，后面每一张都跟改动前一模一样。
    //
    // 它排在最前面是因为参赛评的是"这东西怎么接进真实业务"，
    // 而这一张一句话不用说就答了那个问题。
    await call('Page.navigate', { url: `${server.base}/?from=miaoxiang` });
    await waitFor(call, `document.getElementById('entry')?.classList.contains('on')`,
      '妙想入口卡', 25000);
    await sleep(500);
    await 存图(call, 'phone-00-妙想入口.png');
    await 开局前钉住老邵(call, server.base);
    await waitFor(call, `document.getElementById('transfer')?.classList.contains('on')`,
      '转账确认屏（回到正常流程）', 25000);
    await sleep(500);

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
    await 钉住老邵(call);
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

    console.log(`\n打一局（离线态，${轮数汉字}轮）：`);
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
      标题: `他正要按下确认。<br>你有${轮数汉字}轮，<em>去劝另一个他。</em>`,
      说明: '风险提示要人先承认自己被骗——正在转账的人恰恰最不肯承认。'
        + '角色对调不要求他承认任何事：他只需要去劝一个和他处境一模一样的人。',
      标签: ['6 个诈骗场景', '28 个人格变体', `${MAX_ROUNDS} 轮对局`, '判分由程序算，不由模型打'],
    });
    await 出封面(cdp, 'cover-02-异动触发.png', {
      图: 转账,
      眉: '它什么时候弹出来',
      标题: '清仓、大额转出——<br><em>按下确认之前的最后一帧。</em>',
      说明: '证券资金只能在本人同名账户之间实时划转。这一帧是券商能看见的最后一帧，'
        + '下一秒钱去哪儿账户上再也看不到——所以干预只能发生在按下之前。',
      标签: ['账户异动触发', '不打断交易主链路', `${轮数汉字}轮`, '对照组可比'],
    });
    // 「怎么接进去」那一张：四格里只有一格是真的，徽章与底注一起说死
    await 出接入图(cdp, 'cover-03-怎么接进去.png');
  } finally {
    cdp.close();
    chrome.kill();
    server.stop();
  }

  console.log(`\n全部落在 ${path.relative(process.cwd(), OUT)}/`);
  排上传盘();
}

/** 平台只收 **12 张图 + 1 个视频**，而这个脚本出 15 张。
 *
 *  ## 为什么要有这一步
 *
 *  少哪三张、剩下的按什么顺序排，是**每次交卷都要重做一遍的判断**，
 *  而它此前只存在于 CONTEST 那张表的措辞里。轮数那次已经证明了：
 *  写在文档里的东西不会跟着代码走（tools/rounds.mjs 顶部记着那次）。
 *  所以把它变成一个目录——`dist/materials/上传/`，拖进去就是那 12 张，
 *  文件名自带 01…12，平台的文件选择器按名排序，**顺序不用再靠人记**。
 *
 *  ## 顺序的依据（CONTEST §6.2 的漏斗）
 *
 *  第一张承担全部点击转化，所以是封面（16:10，弹幕跑道已经让出来了）。
 *  紧跟着两张回答评审真正奖励的那个问题——"它怎么接进真实业务流程"：
 *  一张画接入形态，一张画它在整条处置链的哪一格。之后才是叙事：
 *  你自己那一笔 → 被拦下 → 坐到对面 → 打 → 结果 → 复盘 → 回到你自己那一笔。
 *
 *  ## 砍掉的三张，以及为什么是它们
 *
 *  · `phone-06-对局中` —— **和封面重复**。封面里嵌的就是这一屏，
 *    而且带着标题和标签，比单张信息量大。12 格里不该有一格是复读。
 *  · `phone-04-客户档案` —— `phone-03-异动预警` 已经说清"你手上只有账户
 *    那一侧"，档案页是同一件事的细节页。
 *  · `card-分享卡` —— 卡面主角（差 2 倍）`phone-09` 讲得更大更清楚；
 *    卡上那个二维码指向展示页，而看图的人**已经在展示页上了**。
 *
 *  三张都还在 `dist/materials/` 里，随时能换回来——改的是这张清单，
 *  不是重跑截图。 */
function 排上传盘() {
  //: 平台上限。写成常量是为了让下面那句断言有个名字可指。
  const 上限 = 12;
  const 上传 = [
    'cover-01-对局中.png',            // 封面：唯一有辨识度的画面 + 主钩子
    'phone-00-妙想入口.png',          // 接入形态：它作为一张技能卡长什么样
    'cover-03-怎么接进去.png',        // 它在整条处置链的哪一格（四格只有一格是真的）
    'cover-02-异动触发.png',          // 什么时候弹出来
    'phone-01-转账确认.png',          // 冷开场：这一笔是你自己的
    'phone-02-被拦下.png',            // 角色对调那一刻
    'phone-03-异动预警.png',          // 你手上只有账户那一侧
    'phone-05-七把钥匙.png',          // 开局只给词汇，不给时机与分值
    'phone-07-结局.png',              // 结局不另起一块界面
    'phone-08-复盘首屏.png',          // 他最后按没按下确认
    'phone-09-同一句话换个时候说.png', // 全作品唯一竞品没有的判据
    'phone-10-回到你自己那一笔.png',   // 冷开场那个环在这儿合上
  ];
  if (上传.length !== 上限) {
    // 静默出 13 张 = 交卷当天在平台上被拒一次，而那时候没人记得该砍哪张
    throw new Error(`上传清单是 ${上传.length} 张，平台只收 ${上限} 张`);
  }
  const 盘 = path.join(OUT, '上传');
  fs.rmSync(盘, { recursive: true, force: true });  // 换过清单之后不留旧编号
  fs.mkdirSync(盘, { recursive: true });
  console.log(`\n上传盘（${上限} 张，按图集顺序编号）：`);
  上传.forEach((名, i) => {
    const 源 = path.join(OUT, 名);
    if (!fs.existsSync(源)) throw new Error(`上传清单里的 ${名} 没有出图`);
    const 新 = `${String(i + 1).padStart(2, '0')}-${名.replace(/^(cover|phone|card)-\d*-?/, '')}`;
    fs.copyFileSync(源, path.join(盘, 新));
    console.log(`  ${新}`);
  });
  console.log(`  → ${path.relative(process.cwd(), 盘)}/  （视频另传 demo-51s.mp4）`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
