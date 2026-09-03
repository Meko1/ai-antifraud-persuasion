/* 录参赛用的演示视频。
 *
 *     node tools/capture_video.mjs            # 落到 dist/materials/demo.mp4
 *
 * ## 为什么不用录屏软件
 *
 * 和 `capture_materials.mjs` 同一个理由，而且更重：**视频要重录的次数比截图
 * 还多**（改一句文案、换一个结局、时长超了要剪）。手工录屏每次都要重走一遍
 * 全流程、重新对齐、重新剪辑，改完文案第二天就对不上了。写成脚本之后，
 * 改完代码跑一次就重出一条。
 *
 * ## 为什么不用 ffmpeg
 *
 * 这台机器上没有，而装它要动系统。**Chrome 自己就能编码**：
 * `MediaRecorder` 从 Chrome 126 起支持 `video/mp4;codecs=avc1`（本机
 * 151 实测支持），配 `canvas.captureStream()` 就是一条完整的编码链路。
 * 于是这个脚本和仓库其余部分一样：**零 npm 依赖、零外部二进制**，
 * 只要一个 Chrome——而 tests/e2e 本来就要它。
 *
 * ## 两步走
 *
 *   一、**录**：真起服务、真开浏览器、真走一遍流程，用 CDP 的
 *       `Page.startScreencast` 收帧。**画面上没有一帧是排出来的**，
 *       全是这个作品自己跑出来的。
 *   二、**编**：另开一页，把帧按剧本铺到 1920×1080 的画布上
 *       （手机屏在左，字幕在右），`MediaRecorder` 录成 MP4。
 *
 * ## 时间是压过的
 *
 * 真打一局要十来分钟，而视频要控制在一分钟内。所以**录的时候不赶，编的时候
 * 按章压缩**：`剧本` 里每一章写明想占几秒，编码时把那一章录到的帧线性映射
 * 到目标时长上。静止的屏幕自然停住（screencast 只在画面变化时发帧），
 * 对话那一段则会被压成 1.5 倍左右——读起来是"利落"，不是"快进"。
 *
 * **`剧本` 的头两章加起来 12 秒**，这是 CONTEST-POSITIONING §六 S5 那条
 * "前 12 秒把角色对调演完"的硬要求，改时长时先看那一条。
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  connect, evaluate, findChrome, launchChrome, newPage, waitFor, waitUntil,
} from '../tests/e2e/cdp.mjs';
import { startServer } from '../tests/e2e/server.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, '..', 'dist', 'materials');

/** 录的时候用的手机尺寸。与 capture_materials.mjs 一致——两套素材是同一台
 *  「手机」上出来的，尺寸不一样会在图集里看出接缝。 */
const PHONE = { width: 390, height: 844, deviceScaleFactor: 2, mobile: true };

/** 成片规格。1920×1080 是各家展示页最不会出岔子的一个。 */
const 成片 = { 宽: 1920, 高: 1080, fps: 25, 码率: 4_000_000 };

/** screencast 收帧的上限宽。手机原图是 780×1688，成片里手机只占 ~470 宽，
 *  收 600 已经绰绰有余——再大只是白白多传几十兆。 */
const 收帧宽 = 600;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 对局里说的话。**照抄 capture_materials.mjs 的前几句**：那几句是对着
 *  `app/offline.py` 的关键词表写的（离线态分类是正则，不是模型），
 *  而且已经验证过能把「钥匙 / 失误 / 时机」三样都演到。
 *
 *  **十二轮要全打完**：不打到结局，复盘里就没有「同一句话，换个时候说」
 *  那一块（`contrastFacts()` 拿不到效力矩阵会返回 null，整块不画），
 *  而那一块正是这支片子的落点。第一版在第 6 轮主动结束，录出来的最后
 *  六秒字幕写着"同一句话，换个时候说"，画面上却是另一块——**说的和演的
 *  对不上，比少一块更糟**。
 *
 *  前 `慢放轮数` 轮一轮一轮看清楚，其余几轮快进带过（`剧本` 里的
 *  `fast` 那一章）——十二轮全按原速，一分钟根本装不下。 */
const 台词 = [
  '陈叔，这三十万本来是打算做什么用的？',
  '我听得出来您这两天挺着急的。',
  '您刚才说是内部消息，可之前您说群里谁都能进，这两句怎么放在一起？',
  '转不转由您决定，我不替您做主，只想请您先回答两个问题。',
  '王老师让您做的这几步，您能自己讲一遍给我听吗？',
  '那笔钱原本是不是给女儿准备的？',
  '他为什么一定要今天三点前？',
  '您刚才说他保本，可他又说不承诺收益，这两句能同时成立吗？',
  '我知道您不容易，这些年攒下来不容易。',
  '这笔钱转过去之后，您打算怎么把它取回来？',
  '正规机构从来不会这么做，这是个局。',
  '最后一件事由您定：今天先不转，明天我陪您一起核一遍，行吗？',
];

/** 前几轮按原速演，之后的快进带过。 */
const 慢放轮数 = 6;

/** 剧本：每一章占几秒、右边那块写什么。
 *
 *  **总时长 48 秒**，留在 60 秒线内。眉是小字，题是大字，注是灰的一行。
 *
 *  **头两章加起来 12 秒**，这是 CONTEST-POSITIONING §六 S5 那条"前 12 秒
 *  把角色对调演完"的硬要求，改时长时先看那一条。
 *
 *  每一章的 `秒` 都尽量贴着它实录的长度（跑完会打印实录/成片的比值）。
 *  静止的几屏拉长一点看不出来，**只有 chat 那一章不行**——它有 SSE 逐句
 *  到的动效，拉慢了一眼看得出。那一章的时长与 `台词` 的条数是一起定的。 */
const 剧本 = [
  { id: 'transfer', 秒: 5, 眉: '这是一个真实存在的时刻',
    题: '你正要转出 10 万', 注: '钱转到自己卡上，合规、正常、不需要理由' },
  { id: 'handoff', 秒: 7, 眉: '而这也是券商能看见的最后一帧',
    题: '接下来十二轮，请你坐到对面', 注: '角色对调：被劝的人，去劝一个和他处境一样的人' },
  { id: 'desk', 秒: 6, 眉: '你手上只有账户那一侧的一条预警',
    题: '有人正要做同样的事', 注: '骗局的一切，都得从他嘴里挖出来' },
  { id: 'primer', 秒: 4, 眉: '开局只给词汇，不给答案',
    题: '你手里有七把钥匙', 注: '给的是动作的名字，不是什么时候用' },
  { id: 'chat', 秒: 11, 眉: '十二轮，信任度是唯一的状态量',
    题: '难的从来不是说什么，是什么时候说', 注: '同一把钥匙，早一轮晚一轮，效果差很远' },
  { id: 'fast', 秒: 3, 眉: '其余几轮快进',
    题: '十二轮打满', 注: '每一轮都在算：命中了哪一把、扎没扎根、时机对不对' },
  { id: 'ending', 秒: 4, 眉: '结局不另起一块界面',
    题: '它就是这段对话里的最后一件东西', 注: '一张转账凭证——劝住没劝住，在屏幕上只有这一个样子' },
  { id: 'review', 秒: 5, 眉: '复盘',
    题: '每一分都是程序按规则表算的', 注: '不是模型打的分——这张表是纯函数，可以离线重跑' },
  { id: 'contrast', 秒: 6, 眉: '这一局判的就是这个差值',
    题: '同一句话，换个时候说', 注: '这是全作品唯一一处竞品没有的判据' },
];

// ── 一、录 ────────────────────────────────────────────────────────────

/** 收帧。返回 `{ 帧, 打点 }`：
 *  · `帧`   —— `{ t, data }`，t 是相对开录的毫秒；
 *  · `打点` —— 章节边界，`录('id')` 打一个。
 *
 *  **screencast 只在画面变化时发帧**，所以静止的那几屏一章只有一两帧。
 *  这正是我们要的：编码时按时间戳补齐，静止就自然停住。 */
async function 开录(cdp, call) {
  const 帧 = [];
  const 打点 = [];
  const 起 = Date.now();

  cdp.on('Page.screencastFrame', (p) => {
    // **时间戳取 Chrome 给的采集时刻，不是 Node 收到的时刻。**
    // 收到的时刻会在忙的时候整体落后：ack 是异步的，帧在 WebSocket 里排队，
    // 而 Node 这边正 await 着一串 evaluate。用收到时刻铺出来的片子，
    // 画面会比字幕慢一大截——实测第 41 秒的字幕已经走到"结局"，
    // 画面还停在第 1 轮。`metadata.timestamp` 是 Unix 秒，与 `起` 同一把尺。
    const 采 = p.metadata && p.metadata.timestamp;
    帧.push({ t: 采 ? 采 * 1000 - 起 : Date.now() - 起, data: p.data });
    // **必须 ack，否则 Chrome 只发第一帧就不动了。** 它靠这个做背压
    call('Page.screencastFrameAck', { sessionId: p.sessionId }).catch(() => {});
  });

  await call('Page.startScreencast', {
    format: 'jpeg', quality: 85, maxWidth: 收帧宽, maxHeight: 收帧宽 * 4, everyNthFrame: 1,
  });

  return {
    帧,
    打点,
    录(id) { 打点.push({ id, t: Date.now() - 起 }); },
    async 停() {
      await call('Page.stopScreencast');
      打点.push({ id: '__end', t: Date.now() - 起 });
    },
  };
}

/** 开局**之前**就钉住老陈。
 *
 *  下面那个 `钉住老陈()` 只能在工作台那一屏用（「换一位客户」在那儿），
 *  而**转账屏与拦截屏在它之前**——不提前钉，片子前 12 秒的拦截屏印着
 *  「你是**她**的投资顾问」，第 20 秒起聊天窗口里却坐着老陈。实测第一版
 *  就是这样，一支片子里换了个人。
 *
 *  用的仍然是界面自己的那把钥匙：`aap.pick.sid` 正是「换一位客户」写下的
 *  那个键，这里只是提前到首屏之前写。**没有为出素材改任何产品行为**。 */
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

/** 工作台上那把补救钥匙。台词里写着"陈叔""王老师"，不钉住就会出现拿老陈的
 *  台词去劝周淑琴的画面。开局前那一手生效时这里直接返回——留着它是兜底：
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

/** 打一轮。`读秒` 是他回完话之后停多久——**这不是排版参数，是节奏**：
 *  离线态的回话几乎是瞬时的（一轮 0.75s），不停的话这一章只录到四秒多，
 *  铺到成片十四秒就成了 0.3 倍速慢放，SSE 逐句到的动效一眼看得出被拉过。
 *  停一下既让观众读得完，也让实录长度贴住成片长度。 */
async function 打一轮(call, 说, 读秒 = 1500) {
  const 之前 = await evaluate(call, `document.querySelectorAll('#thread .msg.them').length`);
  await evaluate(call, `(() => {
    const el = document.getElementById('say');
    el.value = ${JSON.stringify(说)};
    el.dispatchEvent(new Event('input', { bubbles: true }));
    document.getElementById('composer').requestSubmit();
  })()`);
  await waitFor(call, `document.querySelectorAll('#thread .msg.them').length > ${之前}
    || !!document.querySelector('.review')`, '他回话', 30000);
  await waitFor(call, `!document.querySelector('#thread .bubble.typing')
    || !!document.querySelector('.review')`, '输入气泡收掉', 20000);
  await sleep(读秒);
}

/** 走一遍流程，边走边打点。 */
async function 走一遍(call, 录像) {
  录像.录('transfer');
  await sleep(4500);                       // 让人看清这是一张转账确认单

  await evaluate(call, `document.getElementById('transferGo').click()`);
  await waitFor(call, `!document.getElementById('transferHandoff').hidden`, '拦截那一面');
  录像.录('handoff');
  await sleep(6500);                       // 这一屏是整个作品的落点，给足时间

  // **等按钮解锁再点。** 「坐到对面」换面之后被锁 450ms（`HANDOFF_ARM_MS`），
  // 点在禁用态上什么都不发生，然后卡在下一个 waitFor 上超时
  await waitFor(call, `!document.getElementById('handoffGo').disabled`, '「坐到对面」解锁');
  await evaluate(call, `document.getElementById('handoffGo').click()`);
  await waitFor(call, `document.querySelector('.screen.on')?.id !== 'transfer'`, '离开转账屏', 25000);
  await waitFor(call, `document.getElementById('openingTitle')?.textContent.length > 0`,
    '工作台', 25000);
  await 钉住老陈(call);
  录像.录('desk');
  await sleep(2800);

  await evaluate(call, `document.getElementById('openProfile').click()`);
  await waitFor(call, `document.getElementById('home')?.classList.contains('on')`, '客户档案');
  await sleep(2600);

  await evaluate(call, `document.getElementById('openChen').click()`);
  await waitFor(call, `document.getElementById('primer')?.classList.contains('on')`, '课程表');
  录像.录('primer');
  await sleep(3600);

  await evaluate(call, `document.getElementById('primerGo').click()`);
  await waitFor(call, `document.getElementById('chat')?.classList.contains('on')`, '聊天屏');
  await waitFor(call, `document.querySelectorAll('#thread .msg').length >= 2`, '开场两条', 30000);
  录像.录('chat');
  await sleep(900);
  for (let i = 0; i < 台词.length; i += 1) {
    if (await evaluate(call, `!!document.querySelector('.review')`)) break;
    // 前几轮一轮一轮看清楚，之后快进带过——十二轮全按原速，一分钟装不下。
    // **但一定要打满**：不走到结局，复盘里就没有落点那一块（见 `台词` 顶部）
    if (i === 慢放轮数) { 录像.录('fast'); }
    await 打一轮(call, 台词[i], i < 慢放轮数 ? 1500 : 0);
  }

  // 结局不另起一块 UI，它就是这段对话里的最后一件东西（chat.js `finish`）
  await waitFor(call, `!!document.querySelector('.endcta') || !!document.querySelector('.review')`,
    '结局那一帧', 40000);
  录像.录('ending');
  await sleep(4000);
  if (await evaluate(call, `!!document.querySelector('.endcta')`)) {
    await evaluate(call, `document.querySelector('.endcta').click()`);
  }
  await waitFor(call, `!!document.querySelector('.review')`, '复盘页', 25000);
  录像.录('review');
  await sleep(4600);

  // 滚到「同一句话，换个时候说」那一块——它是这个作品的招牌判据，也是落点。
  // **滚不到就直接报错**：字幕在这一章写着这句话，画面上却是另一块的话，
  // 说的和演的对不上，比少一块更糟（第一版就是这么错的，见 `台词` 顶部）。
  const 滚到了 = await evaluate(call, `(() => {
    const w = document.getElementById('contrastWrap');
    if (!w || w.hidden) return false;
    w.scrollIntoView({ block: 'center', behavior: 'smooth' });
    return true;
  })()`);
  if (!滚到了) {
    throw new Error('复盘里没有「同一句话，换个时候说」那一块——'
      + '多半是这一局没走到结局（contrastFacts 拿不到效力矩阵就整块不画）');
  }
  录像.录('contrast');
  await sleep(5500);
}

// ── 二、编 ────────────────────────────────────────────────────────────

/** 按剧本把录到的帧铺成一条定长时间轴。
 *
 *  每一章取 `秒 × fps` 个采样点，把该章录到的那段线性映射上去：
 *  静止的屏幕重复同一帧（看着就是停住），对话那段被压紧。
 *
 *  返回 `{ 图, 序 }`：`图` 是去重后的帧数据，`序[i]` 是第 i 帧用哪一张。
 *  **去重是必须的**——不去重要往浏览器里塞几百兆重复的 base64。 */
function 铺时间轴(帧, 打点) {
  const 索引 = new Map();
  const 图 = [];
  const 序 = [];
  const 字 = [];

  const 取图 = (data) => {
    if (!索引.has(data)) { 索引.set(data, 图.length); 图.push(data); }
    return 索引.get(data);
  };

  const 报告 = [];
  for (const 章 of 剧本) {
    const i = 打点.findIndex((m) => m.id === 章.id);
    if (i < 0) throw new Error(`剧本里的「${章.id}」没有被录到`);
    const t0 = 打点[i].t;
    const t1 = 打点[i + 1] ? 打点[i + 1].t : 帧[帧.length - 1].t;
    // 实录与成片的比值。**只有 chat 那一章要求贴近 1**（它有动效），
    // 静止的几屏拉长看不出来。跑完打出来，下次调时长时有据可依
    报告.push({ id: 章.id, 实录: (t1 - t0) / 1000, 成片: 章.秒, 倍: (t1 - t0) / 1000 / 章.秒 });
    const n = Math.max(1, Math.round(章.秒 * 成片.fps));
    for (let k = 0; k < n; k += 1) {
      const t = t0 + ((t1 - t0) * k) / n;
      // 该时刻画面上是哪一帧：最后一个 t 不晚于它的
      let 用 = 帧[0];
      for (const f of 帧) { if (f.t <= t) 用 = f; else break; }
      序.push(取图(用.data));
      字.push(剧本.indexOf(章));
    }
  }
  return { 图, 序, 字, 报告 };
}

/** 编码那一页。手机屏在左，字幕在右，`MediaRecorder` 录成 MP4。 */
function 编码页HTML() {
  return `<!doctype html><meta charset="utf-8"><style>
  html, body { margin: 0; background: #0d0e11; }
  canvas { display: block; }
  </style><canvas id="c" width="${成片.宽}" height="${成片.高}"></canvas>`;
}

const 编码脚本 = `
window.__v = {
  图: [],        // base64 JPEG，去重后的
  位: [],        // 解码好的 ImageBitmap
  序: [],
  字: [],
  剧本: null,
};

/** 分批收帧数据：一次性塞几十兆的表达式进 Runtime.evaluate 会很难看。 */
window.__收图 = (批) => { window.__v.图.push(...批); return window.__v.图.length; };

window.__备料 = async (序, 字, 剧本) => {
  const v = window.__v;
  v.序 = 序; v.字 = 字; v.剧本 = 剧本;
  v.位 = await Promise.all(v.图.map(async (b64) => {
    const r = await fetch('data:image/jpeg;base64,' + b64);
    return createImageBitmap(await r.blob());
  }));
  return v.位.length;
};

/** 圆角矩形路径。**没有用 roundRect()**：它在旧一点的 Chrome 上没有，
 *  而这个脚本要能在 CI 的浏览器上跑。 */
function 圆角(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** 一行行地画，自己断行——canvas 不会自动折。中文按字断。 */
function 折行(ctx, 文, 宽) {
  const 行 = [];
  let 当前 = '';
  for (const ch of 文) {
    if (ctx.measureText(当前 + ch).width > 宽 && 当前) { 行.push(当前); 当前 = ch; }
    else 当前 += ch;
  }
  if (当前) 行.push(当前);
  return 行;
}

/* 发起编码，**立刻返回**——Node 那边靠轮询 window.__状态 等它完。
 * 理由写在调用处：CDP 客户端给每条命令钉了 30s 超时，而这一步要跑 ~50s。
 * （这一整段是模板串，里面不许出现反引号——封面那段 HTML 同理。） */
window.__开录 = (规格) => {
  window.__状态 = null;
  window.__录(规格)
    .then((r) => { window.__状态 = r; })
    .catch((e) => { window.__状态 = { 错: String(e && e.stack || e) }; });
  return true;
};

window.__录 = async ({ 宽, 高, fps, 码率 }) => {
  const v = window.__v;
  const cv = document.getElementById('c');
  const ctx = cv.getContext('2d');
  const 字体 = '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif';

  // 手机屏放左边，留出上下各 40px
  const 屏高 = 高 - 80;
  const 屏宽 = Math.round(屏高 * 390 / 844);
  const 屏x = 132;
  const 屏y = 40;

  const 文x = 屏x + 屏宽 + 108;
  const 文宽 = 宽 - 文x - 120;

  // 背景那点光，跟封面同一套。**建一次就够**：每帧新建一个渐变，
  // 一千多帧下来纯属白烧主线程，而主线程一忙，Node 那边的轮询就拿不到回复
  // （CDP 的 Runtime.evaluate 要主线程来答，实测直接把整步拖超时）。
  const 底光 = ctx.createRadialGradient(宽 * 0.12, 0, 0, 宽 * 0.12, 0, 宽 * 1.1);
  底光.addColorStop(0, '#22252c');
  底光.addColorStop(0.58, '#131417');
  底光.addColorStop(1, '#0d0e11');

  // 字也是**每章排一次版**，不是每帧。折行要 measureText，那是同一笔开销
  const 排好 = v.剧本.map((章) => {
    ctx.font = '700 58px ' + 字体;
    const 题行 = 折行(ctx, 章.题, 文宽);
    ctx.font = '400 27px ' + 字体;
    return { 眉: 章.眉, 题行, 注行: 折行(ctx, 章.注, 文宽) };
  });

  function 画(i) {
    const 章 = 排好[v.字[i]];
    ctx.fillStyle = '#0d0e11';
    ctx.fillRect(0, 0, 宽, 高);
    ctx.fillStyle = 底光;
    ctx.fillRect(0, 0, 宽, 高);

    // 手机：先画阴影再画图，圆角裁切
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,.55)';
    ctx.shadowBlur = 48;
    ctx.shadowOffsetY = 14;
    ctx.fillStyle = '#000';
    圆角(ctx, 屏x, 屏y, 屏宽, 屏高, 34);
    ctx.fill();
    ctx.restore();

    ctx.save();
    圆角(ctx, 屏x, 屏y, 屏宽, 屏高, 34);
    ctx.clip();
    const bm = v.位[v.序[i]];
    if (bm) ctx.drawImage(bm, 屏x, 屏y, 屏宽, 屏高);
    ctx.restore();

    // 右边那块字
    let y = 高 / 2 - 128;
    ctx.textAlign = 'left';
    ctx.fillStyle = '#e06a2b';
    ctx.font = '600 27px ' + 字体;
    ctx.fillText(章.眉, 文x, y);
    y += 76;

    ctx.fillStyle = '#fff';
    ctx.font = '700 58px ' + 字体;
    for (const 行 of 章.题行) { ctx.fillText(行, 文x, y); y += 74; }

    y += 22;
    ctx.fillStyle = 'rgba(255,255,255,.62)';
    ctx.font = '400 27px ' + 字体;
    for (const 行 of 章.注行) { ctx.fillText(行, 文x, y); y += 44; }

    // 底部进度条：让人知道还剩多少，别以为卡住了
    const p = (i + 1) / v.序.length;
    ctx.fillStyle = 'rgba(255,255,255,.10)';
    ctx.fillRect(文x, 高 - 96, 文宽, 3);
    ctx.fillStyle = '#e06a2b';
    ctx.fillRect(文x, 高 - 96, 文宽 * p, 3);
  }

  画(0);
  // **captureStream(fps)，不是 captureStream(0) + requestFrame()。**
  // 手动那条路看着更"精确"，实测却在这个负载下大量丢帧：同样 1275 次调用，
  // 成片只剩 35 秒、200 KB。自动采样反而稳（墙钟与成片时长对得上）。
  //
  // 画面与字幕对不齐**不是这里的毛病**——那是收帧时用了"Node 收到的时刻"
  // 当时间戳，见 Node 那边 开录() 里那段注。改用 Chrome 给的采集时刻就齐了。
  // （这一整段是模板串，里面不许出现反引号。）
  const 流 = cv.captureStream(fps);
  const 型 = ['video/mp4;codecs=avc1.42E01E', 'video/mp4', 'video/webm;codecs=vp9']
    .find((t) => MediaRecorder.isTypeSupported(t));
  const 块 = [];
  const rec = new MediaRecorder(流, { mimeType: 型, videoBitsPerSecond: 码率 });
  rec.ondataavailable = (e) => { if (e.data.size) 块.push(e.data); };
  const 完 = new Promise((r) => { rec.onstop = r; });
  rec.start();

  // 仍然**按墙钟走**：MediaRecorder 给每一帧打的是真实时间戳，
  // 所以每一帧都对着开录时刻算它该在什么时候出现，别让它越画越快。
  const 起 = performance.now();
  for (let i = 0; i < v.序.length; i += 1) {
    const 该在 = (i * 1000) / fps;
    const 还差 = 该在 - (performance.now() - 起);
    if (还差 > 0) await new Promise((r) => setTimeout(r, 还差));
    // **一模一样的帧不重画。** 静止的那几屏一章只录到一两张图，
    // 连着几十帧画的是同一个东西——captureStream(fps) 反正是自己去采画布，
    // 画不画都一样。只在图或章变了、以及进度条该动了的时候才落笔。
    const 变了 = i === 0 || v.序[i] !== v.序[i - 1] || v.字[i] !== v.字[i - 1];
    if (变了 || i % 8 === 0) 画(i);
  }
  const 墙钟 = (performance.now() - 起) / 1000;
  await new Promise((r) => setTimeout(r, 400));  // 让最后一帧进得去
  rec.stop();
  await 完;

  const buf = await new Blob(块, { type: 型 }).arrayBuffer();
  const u8 = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < u8.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  }
  window.__片 = btoa(s);
  return { 型, 字节: u8.length, base64长: window.__片.length, 墙钟 };
};

/** 成片可能有十几兆，base64 出来二十几兆——一次性从 Runtime.evaluate 取
 *  容易碰上大小限制，分段取。 */
window.__取片 = (从, 长) => window.__片.substr(从, 长);
`;

// ── 主流程 ────────────────────────────────────────────────────────────

async function main() {
  fs.mkdirSync(OUT, { recursive: true });

  const chromePath = findChrome();
  if (!chromePath) {
    console.error('找不到 Chrome。装一个，或用 CHROME_PATH 指过去。');
    process.exit(1);
  }

  console.log('起服务（离线演示态）…');
  const server = await startServer();
  const chrome = await launchChrome(chromePath);
  const cdp = await connect(chrome.wsUrl);

  try {
    const { call } = await newPage(cdp);
    await call('Emulation.setDeviceMetricsOverride', PHONE);
    // 首屏之前就钉住，否则拦截屏那句身份行说的是另一位客户
    await 开局前钉住老陈(call, server.base);
    await waitFor(call,
      `!document.getElementById('transferGo')?.disabled`, '转账屏就绪', 25000);
    await sleep(600);

    console.log('走一遍并收帧…');
    const 录像 = await 开录(cdp, call);
    await 走一遍(call, 录像);
    await 录像.停();
    console.log(`  收到 ${录像.帧.length} 帧，实录 ${(录像.打点.at(-1).t / 1000).toFixed(1)}s`);

    const { 图, 序, 字, 报告 } = 铺时间轴(录像.帧, 录像.打点);
    const 秒 = 序.length / 成片.fps;
    console.log(`  铺成 ${序.length} 帧 / ${秒.toFixed(1)}s（去重后 ${图.length} 张）`);
    for (const r of 报告) {
      // chat 那一章离 1.0 太远就提醒：它是全片唯一有动效的一段
      const 警 = r.id === 'chat' && (r.倍 < 0.8 || r.倍 > 1.25) ? '  ← 调 剧本/台词' : '';
      console.log(`    ${r.id.padEnd(9)} 实录 ${r.实录.toFixed(1)}s → 成片 ${r.成片}s`
        + `  ×${r.倍.toFixed(2)}${警}`);
    }

    console.log('编码（这一步按墙钟走，大约就是成片时长）…');
    const 编 = await newPage(cdp);
    await 编.call('Emulation.setDeviceMetricsOverride',
      { width: 成片.宽, height: 成片.高, deviceScaleFactor: 1, mobile: false });
    await 编.call('Page.navigate', {
      url: 'data:text/html;charset=utf-8,' + encodeURIComponent(编码页HTML()),
    });
    await waitFor(编.call, `!!document.getElementById('c')`, '画布');
    await evaluate(编.call, 编码脚本);

    for (let i = 0; i < 图.length; i += 8) {
      await evaluate(编.call, `window.__收图(${JSON.stringify(图.slice(i, i + 8))})`);
    }
    await evaluate(编.call,
      `window.__备料(${JSON.stringify(序)}, ${JSON.stringify(字)}, ${JSON.stringify(剧本)})`);

    // **不能 await 这个 promise。** `cdp.mjs` 的 `send` 给每条命令钉了 30s
    // 超时，而编码按墙钟走，本来就要跑够成片那么久（~50s）。那个超时是
    // E2E 共用的，不该为出素材去放宽它——**改成发出去就不管，再轮询状态**。
    await evaluate(编.call, `window.__开录(${JSON.stringify(成片)})`);
    // **先干等，再去问。** 编码整段占着页面主线程，而 `Runtime.evaluate`
    // 要主线程来答——边编边轮询的话，每一问都等不到回复（实测把这一步
    // 直接拖到超时）。按成片时长睡过去，剩下的才是真该轮询的部分。
    await sleep(秒 * 1000 + 3000);
    const 完 = await waitUntil(async () => {
      const s = await evaluate(编.call, `window.__状态 && JSON.stringify(window.__状态)`);
      return s ? JSON.parse(s) : null;
    }, { what: '编码完成', timeout: 300000, every: 1000 });
    if (完.错) throw new Error(`编码时抛异常：${完.错}`);
    const { 型, base64长, 墙钟 } = 完;
    // **编码是按墙钟走的，这个数就该等于成片时长。** 对不上说明画一帧比
    // 一帧的预算还慢，循环追不上时间表——那时候成片会整体加速。
    if (Math.abs(墙钟 - 秒) > 1.5) {
      console.log(`  ⚠ 编码墙钟 ${墙钟.toFixed(1)}s vs 目标 ${秒.toFixed(1)}s —— 画得太慢，成片会被压快`);
    }

    let b64 = '';
    const 段 = 4_000_000;
    for (let i = 0; i < base64长; i += 段) {
      b64 += await evaluate(编.call, `window.__取片(${i}, ${段})`);
    }

    const 后缀 = 型.startsWith('video/mp4') ? 'mp4' : 'webm';
    const 路径 = path.join(OUT, `demo-${Math.round(秒)}s.${后缀}`);
    fs.writeFileSync(路径, Buffer.from(b64, 'base64'));
    const mb = (fs.statSync(路径).size / 1024 / 1024).toFixed(1);
    console.log(`\n  ✓ ${path.basename(路径)}  ${mb} MB  ${秒.toFixed(1)}s  ${型}`);
    console.log(`\n落在 ${OUT}`);
  } finally {
    cdp.close();
    chrome.kill();
    server.stop();
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
