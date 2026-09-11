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
 *   phone-*.png     手机版式原图（390×844 @2x），内页拿它排版，也留着单独用
 *   card.png        分享卡原图，直接从 canvas 取，不经过截图
 *   cover-*.png     16:10 封面（1920×1200），大赛「封面图建议 16:10」那一条
 *   page-*.png      16:10 内页：手机原图 + 一句话说明，与封面同一套版式
 *
 * **上传盘那十二格全部是 16:10**（2026-09-08 起）。此前有九格直接传手机
 * 原图，在展示页那个横着的看图器里两边大片留黑，而且不解释自己是什么。
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
  // **不许在这里说股票。** 老邵是健康恐吓那一场（app/scenario.py 的 shao：
  // 病灶 / 疗程 / 体检），他赎的是持有九年的稳健理财，钱是交给"健康管理"
  // 机构的——嘴里冒出「投资有风险，入市需谨慎」是把另一场的台词念到了
  // 这一场里。而这句话正好印在封面那张聊天截图上：**承办方自己就是券商**，
  // 一张券商出品的封面上写着风险提示口诀去劝一个买保健品的人，
  // 谁看都是串场。换成同样吃「说教」那条罚则的健康话术
  // （app/offline.py `_PENALTY_RULES` 的 preach：「给你科普」），
  // 罚则一格不变，场景对上了。
  '我给你科普一下，那种疗程根本治不了病，您得为自己负责。',
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

/** 每张手机原图的 base64，键是文件名。内页要拿它们再排一次版，
 *  而这些图是流程一路走下来顺手截的——重截一遍就要把整局再打一遍。 */
const 原图 = {};

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
  原图[文件名] = data;
  return data;
}

async function 打一轮(call, 说) {
  // **先等全屏层散掉再发下一句（2026-09-11）。** 第一轮结束后会盖上一层
  // 「时机判读」全屏揭晓（chat.js 的 `showRevealStage`，4.2 秒自动消失），
  // 命中钥匙那一下也会盖一层。盖着的时候 `requestSubmit()` 发不出去——
  // **症状极具迷惑性**：第 1 轮明明成功了（对方回了话），卡的是第 2 轮，
  // 而 `say` 里那句话还原样躺着。不等它，这条素材线就永远停在第 2 轮。
  await waitFor(call, `!document.querySelector('.reveal-stage')`, '全屏揭晓散掉', 12000);
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
 *  此前工作台上还有一把补救钥匙（「换一位客户」），而**转账确认屏与拦截屏
 *  在它之前**；2026-09-11 快速进场之后工作台不再在路上，那把补救连同它的
 *  函数一起删了，钉老邵**只剩这一处**。于是 2026-08-30 之前出的素材里
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

/** 封面那一页的 HTML。手机原图以 data URL 塞进去，排完版再截一次。 */
/** 封面版式。`裁屏` 打开时右边只放屏幕上半截（放大），否则放整台手机。
 *
 *  ## 为什么要有 `裁屏` 这个开关（2026-09-08）
 *
 *  **列表页的缩略图比这张图小得多**：展示页的作品卡里，这张 1920 宽的图
 *  实际按四百多像素渲染。整台手机缩到那个尺寸之后，聊天气泡只剩一片灰点——
 *  画面里最该被看见的那件事（这是一场**对话**，对面那个人正在防着你）
 *  在列表里一点都读不出来，而列表正是决定点不点进来的地方。
 *
 *  所以对局那张改成只截屏幕上半截放大来放：状态条、「戒备」、轮次、
 *  头几条气泡，缩略图里仍然认得出。`phone-01` 那张不能这么裁——
 *  它的戏在底部那颗「确认转出」上，裁掉上半截以外的部分等于把戏裁没了。
 */
/* ── 数据条（2026-09-11 加）──────────────────────────────────────────────
 *
 * 所有者定的方向是"更像券商专业工具"。药丸标签和方格数字的差别不是好看
 * 不好看，是**读起来是谁**：圆角药丸是品牌海报的语汇，等宽数字加细分隔线
 * 是仪表盘的语汇。同一组事实（6 场景 / 28 人格 / 7 把钥匙 / N 轮）换一种
 * 排法，这一屏就从"一张宣传图"变成"一台仪器的铭牌"。
 *
 * **数字一个都不许是编的**：这四个数分别对应 app/scenario.py 的场景数、
 * app/persona.py 的人格数、keys.js 的钥匙数、rounds.mjs 现读的轮次上限。
 * 轮次那一格必须走 MAX_ROUNDS，写死会被 tests/test_materials_copy.py 拦下，
 * 那条测试正是为这个踩出来的。
 */
function 数据条HTML(数据) {
  if (!数据 || !数据.length) return '';
  return `<div class="stats">${数据
    .map((d) => `<div class="stat"><b>${d.值}</b><i>${d.名}</i></div>`)
    .join('')}</div>`;
}

function 封面HTML({ 图, 眉, 标题, 说明, 标签, 数据, 底注, 裁屏 = false, 字号 = 100 }) {
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
    display: flex; align-items: center; gap: 76px;
    padding: var(--danmu) 96px 0;
    /* 红光不是装饰，是这张图在列表里唯一的颜色。**同排的作品卡几乎全是
       亮色**（吉祥物、3D、浅色界面截图），一张纯黑加暗金的卡在里面是隐形的。
       红取的是产品自己那颗「戒备」徽章的颜色——反诈预警本来就该是红的，
       不是为了艳而艳。暗金留给次要件（眉标、标签），保持与另外两张封面同宗。 */
    background:
      radial-gradient(58% 58% at 82% 44%, rgba(255,91,91,.26) 0%, rgba(255,91,91,0) 66%),
      radial-gradient(40% 40% at 4% 84%, rgba(201,162,39,.10) 0%, rgba(201,162,39,0) 70%),
      radial-gradient(120% 120% at 12% 0%, #22252c 0%, #131417 58%, #0d0e11 100%);
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
  /* 眉标做成药丸：一条细横线在缩略图里什么都不是，一块有底色的小牌子
     至少还是一个认得出的色块 */
  .eyebrow {
    display: inline-flex; align-items: center; gap: 10px;
    font-size: 24px; font-weight: 600; letter-spacing: .18em; color: #e8cf7c;
    padding: 9px 22px 9px 18px; border-radius: 999px; margin-bottom: 36px;
    background: rgba(201,162,39,.12); border: 1px solid rgba(201,162,39,.4);
  }
  .eyebrow::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: #d9b64a; }
  /* 标题分三级，一级比一级大：铺垫（小）→ 赌注（中）→ 反转（最大、红）。
     缩略图缩到四百多像素之后活下来的只有最后那一行，所以最该被看见的
     那句话必须是最大最红的那句，而不是与前半句同粗同色。 */
  /* 字号是每张封面自己给的：最大那一行（em）宽度按字数走，字多的那张
     照搬 100px 会把最后一两个字挤到第三行去（「…最后一 / 帧。」）。
     版式救不了这个，只有字号能。
     （这一段里同样不许出现反引号，理由见上面那条。） */
  h1 {
    font-size: ${字号}px; line-height: 1.14; font-weight: 800; letter-spacing: -.02em;
    margin-bottom: 34px;
  }
  h1 .setup {
    display: block; font-size: .52em; font-weight: 500; letter-spacing: 0;
    color: #cdd1d8; margin-bottom: 20px;
  }
  h1 em {
    display: block; font-style: normal; color: #ff6b6b; font-size: 1.14em;
    margin-top: 16px; text-shadow: 0 10px 70px rgba(255,91,91,.45);
  }
  p {
    font-size: 30px; line-height: 1.6; color: #c7cbd3; max-width: 21em;
    margin-bottom: 34px;
  }
  .tags { display: flex; flex-wrap: wrap; gap: 14px; max-width: 46em; }
  .tags span {
    font-size: 23px; font-weight: 500; padding: 13px 24px; border-radius: 999px;
    border: 1px solid rgba(201,162,39,.35); color: #e6e8ec;
    background: rgba(201,162,39,.07);
  }
  /* 仪表盘那一条：直角、细分隔线、等宽数字。圆角与底色留给 .tags，
     两者不要混用在同一张图上——混了就是既不像海报也不像仪器。 */
  .stats {
    display: flex; align-items: stretch; max-width: 46em;
    border: 1px solid rgba(255,255,255,.14); border-radius: 6px;
    background: rgba(255,255,255,.035);
  }
  .stat {
    flex: 1 1 0; padding: 20px 10px 18px; text-align: center;
    border-left: 1px solid rgba(255,255,255,.10);
  }
  .stat:first-child { border-left: 0; }
  .stat b {
    display: block; font-size: 52px; font-weight: 700; line-height: 1;
    color: #fff; font-variant-numeric: tabular-nums; letter-spacing: -.01em;
  }
  .stat i {
    display: block; margin-top: 9px; font-style: normal;
    font-size: 21px; font-weight: 500; letter-spacing: .1em; color: #9aa1ad;
  }
  /* 底注：仪器铭牌上那行小字。**它承载的是这套素材里最硬的一句主张**
     （判分由程序算、可离线重跑），所以给它单独一行，不挤进数据格里。 */
  .note {
    margin-top: 18px; font-size: 22px; line-height: 1.5; color: #8d94a0;
    letter-spacing: .02em;
  }
  .shot {
    flex: 0 0 auto; position: relative; overflow: hidden;
    width: ${裁屏 ? '680px; height: 840px' : '500px'}; border-radius: 44px;
    border: 1px solid rgba(255,255,255,.18);
    /* 红光走 box-shadow，不走伪元素：这一块 overflow:hidden（裁屏要用），
       伪元素会被自己裁掉，扩散不出去 */
    box-shadow: 0 70px 150px rgba(0,0,0,.65), 0 0 0 14px rgba(255,255,255,.035),
      0 0 200px 40px rgba(255,91,91,.30);
  }
  .shot img { width: 100%; display: block; }
  ${裁屏 ? `/* 硬裁一刀会像截歪了，底边化开就成了"这一屏还在往下走" */
  .shot::after {
    content: ''; position: absolute; inset: auto 0 0 0; height: 90px;
    background: linear-gradient(180deg, rgba(13,14,17,0) 0%, rgba(13,14,17,.92) 78%, #0d0e11 100%);
  }` : ''}
  </style>
  <div class="left">
    <div class="eyebrow">${眉}</div>
    <h1>${标题}</h1>
    <p>${说明}</p>
    ${数据 ? 数据条HTML(数据) : `<div class="tags">${标签.map((t) => `<span>${t}</span>`).join('')}</div>`}
    ${底注 ? `<div class="note">${底注}</div>` : ''}
  </div>
  <div class="shot"><img src="data:image/png;base64,${图}"></div>`;
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

/** 把一段 HTML 铺成 1920×1200 截下来。封面、接入图、内页走的都是这一条。 */
async function 出整页(cdp, 文件名, html) {
  const { call } = await newPage(cdp);
  await call('Emulation.setDeviceMetricsOverride', COVER);
  const { frameTree } = await call('Page.getFrameTree');
  await call('Page.setDocumentContent', {
    frameId: frameTree.frame.id,
    html,
  });
  // 字体与那张 data URL 图都要真的解码完，否则会截到一半空的。
  // 接入图没有 img，那一支直接放行（`!img`）。
  await waitFor(call, `document.fonts.ready.then(() => {
    const img = document.querySelector('img');
    return !img || (img.complete && img.naturalWidth > 0);
  })`, '页面里的图与字体', 20000);
  await sleep(300);
  const { data } = await call('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(OUT, 文件名), Buffer.from(data, 'base64'));
  console.log(`  ✓ ${文件名}  ${Math.round(fs.statSync(path.join(OUT, 文件名)).size / 1024)} KB`);
}

/** 内页：一张手机原图 + 一句话说明它在讲什么，排成与封面同宗的 16:10。
 *
 *  ## 为什么不直接传手机原图（2026-09-08）
 *
 *  上传的十二张里此前有九张是 390×844 的手机截图原图。它们在展示页
 *  那个横着的看图器里两边留着大片黑，**并且一个字都不解释自己是什么**——
 *  评委要自己猜这一屏为什么值得看。而封面那三张是排过版的，
 *  于是同一份材料里三张像作品、九张像随手截的。
 *
 *  内页把这九张也铺进 1920×1200：左边一句话说清这一屏的用处，
 *  右边放整机（**不裁**——内页是点开看的，裁了反而少给信息）。
 *
 *  ## 红光留给两张
 *
 *  红是"这里正在出事"那个颜色（封面那段注释记了它的来历）。
 *  内页默认走暗金，只有转账确认与被拦下那两张给红——
 *  **全都红等于没有红**。
 */
function 内页HTML({ 图, 眉, 标题, 说明, 红光 = false }) {
  const 光 = 红光
    ? 'radial-gradient(56% 56% at 78% 46%, rgba(255,91,91,.24) 0%, rgba(255,91,91,0) 66%)'
    : 'radial-gradient(56% 56% at 78% 46%, rgba(201,162,39,.16) 0%, rgba(201,162,39,0) 66%)';
  const 边光 = 红光 ? 'rgba(255,91,91,.28)' : 'rgba(201,162,39,.22)';
  return `<!doctype html><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  /* 弹幕跑道与封面同一套，理由见 封面HTML 顶部那段。
     （这一段里不许出现反引号：整块 HTML 是模板串，反引号会把它截断。） */
  body {
    --danmu: 210px;
    width: ${COVER.width}px; height: ${COVER.height}px; overflow: hidden;
    display: flex; align-items: center; gap: 96px;
    padding: var(--danmu) 110px 0;
    background: ${光},
      radial-gradient(120% 120% at 12% 0%, #22252c 0%, #131417 58%, #0d0e11 100%);
    color: #fff;
    font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", -apple-system, sans-serif;
  }
  body::before {
    content: ''; position: fixed; inset: 0 0 auto 0; height: var(--danmu);
    background: linear-gradient(180deg, rgba(0,0,0,.55) 0%, rgba(0,0,0,0) 100%);
  }
  .left { flex: 1 1 auto; min-width: 0; }
  .eyebrow {
    display: inline-flex; align-items: center; gap: 10px;
    font-size: 23px; font-weight: 600; letter-spacing: .18em; color: #e8cf7c;
    padding: 9px 22px 9px 18px; border-radius: 999px; margin-bottom: 34px;
    background: rgba(201,162,39,.12); border: 1px solid rgba(201,162,39,.4);
  }
  .eyebrow::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: #d9b64a; }
  /* 内页标题比封面小一档：这一张是点开之后看的，不用再去抢缩略图里那一眼 */
  h1 { font-size: 66px; line-height: 1.26; font-weight: 800; letter-spacing: -.015em; }
  h1 em {
    display: block; font-style: normal; color: #e0c266; margin-top: 10px;
  }
  p {
    font-size: 29px; line-height: 1.7; color: #c7cbd3; max-width: 20em; margin-top: 34px;
  }
  .shot { flex: 0 0 auto; position: relative; }
  /* 宽度是算出来的，不是看着顺眼定的：手机原图 780×1688，弹幕跑道让掉
     210px 之后只剩 990px 高。470px 宽换算过去是 1017px 高——**上下各露
     十几像素在画布外**，整机被削了一圈边。430px 对应 930px，两头各余 30px。 */
  .shot img {
    width: 430px; display: block; border-radius: 40px;
    border: 1px solid rgba(255,255,255,.16);
    box-shadow: 0 66px 140px rgba(0,0,0,.64), 0 0 0 13px rgba(255,255,255,.035),
      0 0 180px 30px ${边光};
  }
  </style>
  <div class="left">
    <div class="eyebrow">${眉}</div>
    <h1>${标题}</h1>
    <p>${说明}</p>
  </div>
  <div class="shot"><img src="data:image/png;base64,${图}"></div>`;
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
    //
    // **等的是 `aria-disabled`，不是 `disabled`（2026-09-07）。** 那把锁为了
    // 屏幕阅读器改成了 `aria-disabled`（`armHandoff()`），`.disabled` 从此
    // 恒为 false——这一行要是不跟着改，它会立刻放行，脚本又落回上面说的
    // 那个超时里，而且这次连"手快"都看不出来。
    await waitFor(call, `!document.getElementById('handoffGo').hasAttribute('aria-disabled')`,
      '「坐到对面」解锁');
    await evaluate(call, `document.getElementById('handoffGo').click()`);
    // ── 动线跟着产品走（2026-09-11 改写）──────────────────────────────
    //
    // 快速进场之后，「坐到对面」**直接落进对话**——工作台（`#opening`）与
    // 客户档案（`#home`）不再是必经之路。旧动线是"等工作台铺好 → 在那儿
    // 换客户 → 点联系客户开打"，三步现在全部落空：`enterGame()` 在
    // `game.entered` 为真时直接 early-return，而 `钉住老邵()` 点的
    // 「换一位客户」会**重开一局**，把刚刚已经开好的会话打乱——症状是
    // 第 1 轮永远等不到回话（实测，两次）。
    //
    // 老邵是 `开局前钉住老邵()` 在页面加载前就钉好的，到这里不需要再补救，
    // 那个补救函数连同它的调用一起删掉了。
    await waitFor(call, `document.getElementById('chat')?.classList.contains('on')`,
      '直接落进对话', 25000);
    await waitFor(call, `document.querySelectorAll('#thread .msg').length >= 2`, '开场两条', 30000);
    await sleep(400);

    // **确认真的是老邵。** 素材全套的台词写死了「邵叔」「那个疗程」「四十五万」，
    // 钉错人这十句会串到别的场景上去（CONTEST §6 记着这条）。与其出一套串场
    // 素材，不如当场断掉。
    const 客户 = await evaluate(call, `document.getElementById('peer')?.textContent || ''`);
    if (!/邵/.test(客户)) throw new Error(`这一局的客户是「${客户}」，不是老邵——素材台词会串场`);

    // 工作台与客户档案仍然是产品里真实存在的两屏，只是不再拦在路上。
    // 这里纯切视图拍照、拍完切回来：**不点任何会改游戏状态的按钮**，
    // 上面那段说的就是点错一颗的代价。
    await evaluate(call, `document.querySelectorAll('.screen').forEach(s => s.classList.toggle('on', s.id === 'opening'))`);
    await sleep(400);
    await 存图(call, 'phone-03-异动预警.png');

    await evaluate(call, `document.querySelectorAll('.screen').forEach(s => s.classList.toggle('on', s.id === 'home'))`);
    await sleep(300);
    await 存图(call, 'phone-04-客户档案.png');

    await evaluate(call, `document.querySelectorAll('.screen').forEach(s => s.classList.toggle('on', s.id === 'chat'))`);
    await sleep(300);

    // ── 七把钥匙这一屏换了入口（2026-09-11）────────────────────────────
    //
    // 快速进场（opening.js 的 `enterChatDirect()`）之后，`#primer` 不再是
    // 默认路径上的一站——`ackTransfer()` 直接把人放进对话，`game.entered`
    // 当场置真，`openChen` 再点也不会回到讲解屏。这里等 `#primer` 会死等
    // 15 秒然后整条素材线挂掉（实测：phone-04 之后就断在这儿）。
    //
    // **内容一个字没少，只是换了个入口**：七把钥匙现在挂在对局中输入框
    // 旁边那颗钥匙按钮上（`#openMethods` → `control.js` 的
    // `openMethodsSheet()`），文案与讲解屏那版一字不差。素材要展示的是
    // 「开局只给词汇、不给时机与分值」这件事，这个抽屉照样说得清楚，
    // 而且它比讲解屏更接近玩家现在真正会看到的样子。
    await evaluate(call, `document.getElementById('openMethods').click()`);
    await waitFor(call, `!!document.querySelector('.sheet')`, '七把钥匙抽屉');
    await sleep(300);
    // 文件名跟着屏上那句走（2026-08-30 从「七种问法」改成「七把钥匙」：
    // 七把里有四把不是问法，复盘本来就管它们叫钥匙）
    await 存图(call, 'phone-05-七把钥匙.png');

    // 关掉抽屉再开打，否则后面每一张都蒙着这一层
    await evaluate(call, `document.querySelector('.sheet-cancel')?.click()`);
    await waitFor(call, `!document.querySelector('.sheet')`, '抽屉收起');
    await sleep(300);

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

    // 时机对照那一块是全作品唯一竞品没有的判据，单独给它一张。
    //
    // **锚点是 `#contrastExplorer`，不是 `#contrastWrap`（2026-09-07 改）。**
    // 复盘首屏很短，`phone-08` 那一张在滚动位置 0 就已经把整个 `#contrastBox`
    // （引文 + 两个倍数 + 「动作是对的，差的是时候」）装进去了；而
    // `contrastWrap` 居中会把同一段文字再摆一遍，两张图重叠约七成——
    // 正是 §6 砍掉 `phone-06-对局中` 时用的那条理由（「12 格里不该有一格是
    // 复读」），当时没量到这一对。
    //
    // 换成 explorer 之后这一张只剩独有内容：轮次按钮 + 四条档位横条，
    // 也就是"换一轮看看"这件事本身——`phone-08` 只用文字讲了论点，
    // 这一张给的是能上手翻的那份证据。
    //
    // `block: 'start'` 不是 `'center'`：居中会让上一块的尾巴露半行在顶上，
    // 而这一张此前正是开在一句被拦腰切开的免责声明上。对齐到滚动容器顶部，
    // 上面一个字都不剩。
    await evaluate(call, `document.getElementById('contrastExplorer')?.scrollIntoView(
      { block: 'start', behavior: 'instant' })`);
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
    // 这一张是列表页第一眼看到的那张（`排上传盘()` 的第 01 号），
    // 全部点击转化压在它身上。**副文与标签在列表缩略图里是读不到的**，
    // 所以它们只留最短的一句与两枚牌子：字少下来，标题才腾得出字号。
    await 出整页(cdp, 'cover-01-对局中.png', 封面HTML({
      图: 对局图 || 转账,
      裁屏: true,
      眉: 'AI 反诈劝阻',
      标题: `<span class="setup">他正要按下确认。</span>你有${轮数汉字}轮，`
        + '<em>去劝另一个他。</em>',
      说明: '风险提示要人先承认自己被骗——正在转账的人恰恰最不肯承认。',
      // 标题那三行保持不动：#23 那一格 285 次浏览、同屏第二高，说明这句钩子
      // 在抢点击这件事上是有效的，不拿它去换"专业感"。换掉的是下面那一条——
      // 药丸标签改成仪表盘（见 数据条HTML 顶部那段），同一组事实，换一种读法。
      数据: [
        { 值: '6', 名: '场景' },
        { 值: '28', 名: '人格' },
        { 值: '7', 名: '钥匙' },
        { 值: String(MAX_ROUNDS), 名: '轮次' },
      ],
      底注: '判分由程序算，不由模型打 · 纯规则可离线重跑 · 两万局蒙特卡洛标定',
    }));
    await 出整页(cdp, 'cover-02-异动触发.png', 封面HTML({
      图: 转账,
      眉: '它什么时候弹出来',
      字号: 78,  // 最长那一行十一个字，100px 会折到第三行
      // em 现在自己就是一行（display:block），再留 br 会多空一行
      标题: '清仓、大额转出——<em>按下确认之前的最后一帧。</em>',
      说明: '证券资金只能在本人同名账户之间实时划转。这一帧是券商能看见的最后一帧，'
        + '下一秒钱去哪儿账户上再也看不到——所以干预只能发生在按下之前。',
      标签: ['账户异动触发', '不打断交易主链路', `${轮数汉字}轮`, '对照组可比'],
    }));
    // 「怎么接进去」那一张：四格里只有一格是真的，徽章与底注一起说死
    await 出整页(cdp, 'cover-03-怎么接进去.png', 接入图HTML());

    // ── 内页 ────────────────────────────────────────────────────────────
    // 上传盘里剩下九格此前是手机截图原图，理由与做法见 内页HTML 顶部那段。
    // 眉与标题就是 `排上传盘()` 那张清单里每一行后面的注释——**那句话本来
    // 就是"这一张为什么值得占一格"的答案**，此前只有读代码的人看得到。
    console.log('\n内页（手机原图排进 16:10）：');
    const 内页 = [
      ['page-01-接入形态.png', 'phone-00-妙想入口.png', {
        眉: '接入形态',
        // **用的人是账户持有人自己，不是客户经理。**（2026-09-08 改）
        // 这一版初稿写的是「客户经理手边的一张卡」，而全仓上下没有"客户经理"
        // 这个角色：冷开场是「你自己签一笔、按下确认、被拦下来」（index.html
        // 顶部那段注释），「投资顾问」是**对调之后你在模拟里扮的那个身份**，
        // 不是产品的使用者。把这两件事说反，等于把角色对调整个讲拧了。
        标题: '它不另开一个 App，<em>是账户安全里的一张卡。</em>',
        说明: '异动触发，转出前的最后一道。开在券商 App 自己的助手里，不打断交易主链路。',
      }],
      ['page-02-你自己那一笔.png', 'phone-01-转账确认.png', {
        眉: '冷开场',
        标题: '开场先给你看的，<em>是你自己的那一笔。</em>',
        说明: '四十五万，本人同名账户，实时到账。先让你站到按钮前面，再谈别人。',
        红光: true,
      }],
      ['page-03-被拦下.png', 'phone-02-被拦下.png', {
        眉: '角色对调那一刻',
        标题: '你被拦下来，<em>然后被请到对面去坐。</em>',
        说明: '它不要求你承认自己会被骗，只请你去劝一个和你处境一模一样的人。',
        红光: true,
      }],
      ['page-04-账户那一侧.png', 'phone-03-异动预警.png', {
        眉: '你看得见什么',
        标题: '你手上只有<em>账户那一侧。</em>',
        说明: '券商看得见异动，看不见他正在跟谁聊天。这一局就从这个信息差开始。',
      }],
      ['page-05-七把钥匙.png', 'phone-05-七把钥匙.png', {
        眉: '开局给你的东西',
        标题: '只给词汇，<em>不给时机与分值。</em>',
        说明: '七把钥匙开局就摆出来，但哪一把该在第几轮用、值多少分，一个字都不说。',
      }],
      ['page-06-结局.png', 'phone-07-结局.png', {
        眉: '结局',
        标题: '它不另起一块界面，<em>就是这段对话的最后一件东西。</em>',
        说明: '一张转账凭证，一个「看复盘」。劝住没劝住，屏幕上只有这一个样子。',
      }],
      ['page-07-复盘.png', 'phone-08-复盘首屏.png', {
        眉: '复盘',
        标题: '他最后<em>按没按下确认。</em>',
        说明: '判分由程序算，不由模型打——每一格都能倒回去看是哪一轮的哪一句。',
      }],
      ['page-08-换个时候说.png', 'phone-09-同一句话换个时候说.png', {
        眉: '时机对照',
        标题: '动作是对的，<em>差的是时候。</em>',
        说明: '同一句话挪到别的轮次，分数会变。这一格能自己翻着看，不是一句结论。',
      }],
      ['page-09-回到你自己.png', 'phone-10-回到你自己那一笔.png', {
        眉: '回到开头',
        标题: '讲完他，<em>再回到你自己那一笔。</em>',
        说明: '冷开场那个环在这里合上：几分钟前，你也正要按下确认。',
      }],
    ];
    for (const [出, 源, 参数] of 内页) {
      if (!原图[源]) throw new Error(`内页 ${出} 要的 ${源} 没截到`);
      await 出整页(cdp, 出, 内页HTML({ ...参数, 图: 原图[源] }));
    }
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
  //: 十二格全部是排过版的 16:10（2026-09-08 起）。此前有九格是手机截图
  //  原图，理由与改法见 内页HTML 顶部那段。
  const 上传 = [
    'cover-01-对局中.png',        // 封面：唯一有辨识度的画面 + 主钩子
    'page-01-接入形态.png',       // 接入形态：它作为一张技能卡长什么样
    'cover-03-怎么接进去.png',    // 它在整条处置链的哪一格（四格只有一格是真的）
    'cover-02-异动触发.png',      // 什么时候弹出来
    'page-02-你自己那一笔.png',   // 冷开场：这一笔是你自己的
    'page-03-被拦下.png',         // 角色对调那一刻
    'page-04-账户那一侧.png',     // 你手上只有账户那一侧
    'page-05-七把钥匙.png',       // 开局只给词汇，不给时机与分值
    'page-06-结局.png',           // 结局不另起一块界面
    'page-07-复盘.png',           // 他最后按没按下确认
    'page-08-换个时候说.png',     // 全作品唯一竞品没有的判据
    'page-09-回到你自己.png',     // 冷开场那个环在这儿合上
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
    const 新 = `${String(i + 1).padStart(2, '0')}-${名.replace(/^(cover|page|phone|card)-\d*-?/, '')}`;
    fs.copyFileSync(源, path.join(盘, 新));
    console.log(`  ${新}`);
  });
  // **视频文件名不许写死。** `capture_video.mjs` 按实录时长现取名
  // （`demo-${秒}s.mp4`），改一次剧本它就变一次——写死在这儿的话，
  // 交卷当天这行会指着一个不存在的文件，而且跑一万次也发现不了。
  // 同一条教训见 tools/rounds.mjs 顶部那次轮数。
  const 片 = fs.existsSync(OUT)
    ? fs.readdirSync(OUT).filter((f) => /^demo-\d+s\.(mp4|webm)$/.test(f)).sort()
    : [];
  const 片名 = 片.length ? 片.join(' / ') : '（还没跑 capture_video.mjs）';
  console.log(`  → ${path.relative(process.cwd(), 盘)}/  （视频另传 ${片名}）`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
