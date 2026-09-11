/* 六个场景各出一张「完整十轮聊天记录」，落进各自的 `拦下-自留` 目录。
 *
 *     node tools/capture_full_threads.mjs
 *
 * ## 这个脚本要补的缺口
 *
 * `dist/materials-{sid}-拦下-自留/` 六个目录此前各自留着 `phone-06-对局中.png`，
 * 但那一张是**打到第 5 轮时的一帧**（`capture_materials.mjs` 里"第五轮之后
 * 对话已经有来有回，是最能代表这个作品的一帧"）——它展示的是"对局长什么样"，
 * 不是"这一局完整说了什么"。六个场景各自的十轮对话，此前哪个目录里都没有。
 *
 * ## 台词是六套，不是复用同一套
 *
 * `capture_materials.mjs` 的十句台词是**照着老邵（健康恐吓）这一局的具体事实
 * 写的**——"疗程""病灶""四十五万"——钉的场景换了，那十句话就会文不对题
 * （老陈会被问"那个疗程"，读者一眼看出是抄错了）。所以这里六套台词各自照
 * 对应场景 `app/scenarios/*.py` 里的事实重写：金额、人物、说法、称呼
 * 都对得上。
 *
 * 但**判分意图与老邵那套完全对齐**，逐句抄同一个骨架：前三句是故意的
 * 失误（空口断言 / 说教 / 责骂——`app/offline.py` 的 `_PENALTY_RULES`），
 * 后七句依次踩中 anchor_real_purpose / check_understanding /
 * expose_contradiction / support_autonomy / reflect_feeling /
 * socratic_question（兜底）/ 一句不计分的收尾。这套顺序已经在老邵那局
 * 验证过能安全打完十轮不提前拉黑，六套台词照抄同一个节奏，不是另起一套
 * 未经验证的敢冒险。
 *
 * 每句在写的时候都对着 `_KEY_RULES` / `_PENALTY_RULES` 的正则核对过，
 * 不会撞上无关的失误词（例如 zhou 那句提到"涉嫌洗钱案"时刻意避开了
 * "涉案"，否则会被 preach 误判）。
 *
 * ## 「完整十轮」怎么拍出一整张，而不是一屏一屏滚动截
 *
 * `#thread` 平时是 `overflow-y: auto` 的独立滚动区，一屏只装得下当前
 * 那几条。这里在拍照前临时把 `#chat`（整个聊天屏）与 `#thread` 的高度
 * 限制解除（`height:auto` / `overflow:visible`），让浏览器把全部 20 条
 * 消息自然铺开，量出这时候 `#chat.scrollHeight` 需要多高，把设备视口
 * 现改到那个高度再截图——一次性截到完整对话，不拼接、不裁剪。
 * 截完立刻把两处样式和视口都还原，不影响后面继续用（虽然这里每个场景
 * 都会重新导航一次页面，理论上不还原也没事，但别把"反正马上要扔"当习惯）。
 *
 * 复用 tests/e2e 那套 CDP 客户端，理由见 tests/e2e/cdp.mjs 顶部与
 * capture_materials.mjs 顶部——没有 node_modules，也不该有。
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  connect, evaluate, findChrome, launchChrome, newPage, waitFor,
} from '../tests/e2e/cdp.mjs';
import { startServer } from '../tests/e2e/server.mjs';
import { MAX_ROUNDS } from './rounds.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.join(HERE, '..', 'dist');

const PHONE = { width: 390, height: 844, deviceScaleFactor: 2, mobile: true };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 六套台词。**条数必须等于 `MAX_ROUNDS`**——轮数上限现读自
 *  `tools/rounds.mjs`，这里不重复写死一遍（踩过的坑见该文件顶部）。
 *
 *  每套的第 1–3 句依次是空口断言 / 说教 / 责骂，第 4–9 句依次踩
 *  anchor_real_purpose / check_understanding / expose_contradiction /
 *  support_autonomy / reflect_feeling / socratic_question（兜底），
 *  第 10 句不计分，收尾。 */
const 台词表 = {
  chen: [
    '陈叔，这就是个骗局，您千万别再转钱了。',
    '投资有风险，入市需谨慎，您得为自己负责。',
    '您怎么这么糊涂，一个网上认识的老师说的话您也全信？',
    '这三十万本来是留着给孩子办婚礼用的，对吧？',
    '王老师说的那只票，具体是怎么带你们操作的，您给我说说看？',
    '您说这只票是内部消息稳赚不赔，那为什么还要抢在三点前转钱，这不是自相矛盾吗？',
    '转不转由您自己决定，我不会替您做主。',
    '我知道您这段日子压力不小，一个人扛着不容易。',
    '今早已经划走十万了，剩下的两万八，您真要今天全转出去吗？',
    '您再想想吧，这三十万不是小数目。',
  ],
  zhou: [
    '周姨，这就是个骗局，您千万别再转钱了。',
    '投资有风险，入市需谨慎，您得为自己负责。',
    '您怎么这么糊涂，一个电话就说您涉嫌洗钱，您也全信？',
    '这四十八万本来是留着做什么用的？',
    '那份通缉令上写的案子经过，您给我说说看？',
    '您说自己涉嫌洗钱，可警方办案从来不会打电话要求转账核实身份，这不是自相矛盾吗？',
    '转不转由您自己决定，我不会替您做主。',
    '我知道您这段时间提心吊胆，一个人扛着不容易。',
    '这十二年您一次主动操作都没有，今天要一次性转四十八万，您真要这么做吗？',
    '您再想想吧，这四十八万不是小数目。',
  ],
  liu: [
    '刘叔，这就是个骗局，您千万别再转钱了。',
    '投资有风险，入市需谨慎，您得为自己负责。',
    '您怎么这么糊涂，一个网上认识的人就能让您转三十五万？',
    '这三十五万本来是留着给晓芸的，对吧？',
    '那个平台说的稳赚不赔，具体是怎么操作的，您给我说说看？',
    '您说这是正规平台，可为什么钱要转到一个陌生个人账户，这不是自相矛盾吗？',
    '转不转由您自己决定，我不会替您做主。',
    '我知道您这段日子心里不好受，一个人扛着不容易。',
    '这八年您一次主动操作都没有，今天要一次性转三十五万，您真要这么做吗？',
    '您再想想吧，这三十五万不是小数目。',
  ],
  ben: [
    '月娥姐，这就是个骗局，您千万别再垫钱了。',
    '投资有风险，入市需谨慎，您得为自己负责。',
    '您怎么这么糊涂，做任务还要自己往里垫钱，您也肯？',
    '这十一万八本来是留着装修用的，对吧？',
    '那个加速通道具体是怎么让您的钱到账的，您给我说说看？',
    '您说做任务能返利，可为什么返利之前还要您自己先垫钱，这不是自相矛盾吗？',
    '转不转由您自己决定，我不会替您做主。',
    '我知道您这段日子不容易，一个人扛着更难。',
    '前面垫的十一万八还没到账，您今天还要再垫钱吗？',
    '您再想想吧，这十一万八不是小数目。',
  ],
  hang: [
    '这就是个骗局，你千万别再转钱了。',
    '投资有风险，入市需谨慎，你得为自己负责。',
    '你怎么这么糊涂，一个所谓的机构通道就能让你转六十万？',
    '这六十万本来是留着做首付用的，对吧？',
    '那个量化策略具体是怎么帮你稳赚的，你给我说说看？',
    '你说这是机构通道，可为什么开放额度还要靠抢的，这不是自相矛盾吗？',
    '转不转由你自己决定，我不会替你做主。',
    '我知道你这段时间压力不小，一个人扛着不容易。',
    '这五年你一次主动操作都没有，今天要一次性转六十万，你真要这么做吗？',
    '你再想想吧，这六十万不是小数目。',
  ],
  // 与 capture_materials.mjs 的 台词 常量逐字一致——那份已经验证过能
  // 安全打完十轮，这里不重写第二份，直接照抄以防两处不小心走岔。
  shao: [
    '邵叔，这就是个骗局，您千万别再交钱了。',
    // 健康恐吓那一场，不能用荐股场的风险提示口诀（同 capture_materials.mjs）
    '我给你科普一下，那种疗程根本治不了病，您得为自己负责。',
    '您怎么这么糊涂，人家一天看几十个号能有多准？',
    '这四十五万本来是留着做什么用的？',
    '那个疗程具体怎么把病灶清掉的，您说说看？',
    '医院说观察半年，那边说等不得，这不是矛盾吗？',
    '交不交由您决定，我不替您做主。',
    '我知道您这段日子不容易。',
    '这钱交过去万一没效果，您怎么往回要？',
    '您再想想吧，这钱真的不是小数目。',
  ],
};

for (const [sid, lines] of Object.entries(台词表)) {
  if (lines.length !== MAX_ROUNDS) {
    throw new Error(
      `${sid} 的台词是 ${lines.length} 句，MAX_ROUNDS 现在是 ${MAX_ROUNDS}——两者必须相等，`
      + '否则打不满十轮就会提前碰到结局判定，或者十句打完游戏还没结束。',
    );
  }
}

async function 打一轮(call, 说) {
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
  await sleep(150);
}

/** 开局之前把这一局钉在指定场景。做法与 capture_materials.mjs 的
 *  `开局前钉住老邵()` 完全一致，只是 sid 从形参传入而不是写死。
 *  理由见那边的长注：必须在首屏渲染前写好 `aap.pick.sid` 并清掉旧存档，
 *  否则前几屏会是随机某位客户，或者直接续到上一局的聊天页。 */
async function 开局前钉住(call, base, sid) {
  await call('Page.navigate', { url: `${base}/` });
  await waitFor(call, `document.readyState === 'complete'`, '首屏');
  await evaluate(call, `window.__钉 = 1`);
  // **`aap.primer.seen` 也要清（2026-09-07 加）。** 它存在 localStorage，
  // 不像 `aap.game.v1` 那样每次重载都清得掉——六个场景在同一个 Chrome
  // 进程里连着跑，第一个场景一旦走过课程表，`enterGame()` 就会认为
  // "看过了"，后面五个场景点「联系客户」会直接跳进聊天屏，`#primer` 永远
  // 不会变成 `.on`，等它的那句 `waitFor` 会白等 15 秒然后超时——
  // 这正是 zhou 第一次跑时炸的那个坑。
  await evaluate(call, `sessionStorage.removeItem('aap.game.v1');
    sessionStorage.setItem('aap.pick.sid', ${JSON.stringify(sid)});
    localStorage.removeItem('aap.primer.seen')`);
  await call('Page.reload');
  await waitFor(call, `window.__钉 !== 1`, '重载完成（钉住已生效）');
}

/** 拍一张「完整十轮」：解除 `#chat`/`#thread` 的高度裁剪，量出真实内容
 *  高度，把设备视口现改到那个高度，一次性截完整段对话，再复原。 */
async function 存整页(call, 文件名) {
  await evaluate(call, `document.activeElement && document.activeElement.blur()`);

  const 全高 = await evaluate(call, `(() => {
    const chat = document.getElementById('chat');
    const thread = document.getElementById('thread');
    chat.style.height = 'auto';
    thread.style.flex = '0 0 auto';
    thread.style.overflow = 'visible';
    thread.style.maxHeight = 'none';
    return chat.scrollHeight;
  })()`);

  await call('Emulation.setDeviceMetricsOverride', { ...PHONE, height: Math.ceil(全高) + 4 });
  await sleep(300);
  const { data } = await call('Page.captureScreenshot', { format: 'png' });

  // 复原：解除的三条样式与放大的视口都改回去，不带进下一个场景的流程。
  await call('Emulation.setDeviceMetricsOverride', PHONE);
  await evaluate(call, `(() => {
    const chat = document.getElementById('chat');
    const thread = document.getElementById('thread');
    chat.style.height = '';
    thread.style.flex = '';
    thread.style.overflow = '';
    thread.style.maxHeight = '';
  })()`);

  return data;
}

async function 拍一个场景(call, base, sid, lines) {
  await 开局前钉住(call, base, sid);
  await waitFor(call, `document.getElementById('transfer')?.classList.contains('on')`,
    `${sid} 转账确认屏`, 25000);
  await sleep(400);

  await evaluate(call, `document.getElementById('transferGo').click()`);
  await waitFor(call, `!document.getElementById('transferHandoff').hidden`, `${sid} 拦截面`);
  // 「坐到对面」上锁 450ms（`aria-disabled`，见 opening.js 的 `armHandoff()`）。
  await waitFor(call, `!document.getElementById('handoffGo').hasAttribute('aria-disabled')`,
    `${sid} 「坐到对面」解锁`);
  await evaluate(call, `document.getElementById('handoffGo').click()`);
  await waitFor(call, `document.getElementById('openingTitle')?.textContent.length > 0`,
    `${sid} 工作台铺好`, 25000);

  await evaluate(call, `document.getElementById('openProfile').click()`);
  await waitFor(call, `document.getElementById('home')?.classList.contains('on')`, `${sid} 客户档案`);
  await evaluate(call, `document.getElementById('openChen').click()`);
  await waitFor(call, `document.getElementById('primer')?.classList.contains('on')`, `${sid} 课程表`);
  await evaluate(call, `document.getElementById('primerGo').click()`);
  await waitFor(call, `document.getElementById('chat')?.classList.contains('on')`, `${sid} 聊天屏`);
  await waitFor(call, `document.querySelectorAll('#thread .msg').length >= 2`, `${sid} 开场两条`, 30000);
  await sleep(400);

  let 打满 = true;
  for (let i = 0; i < lines.length; i += 1) {
    if (await evaluate(call, `!!document.querySelector('.review')`)) {
      打满 = false;
      console.log(`\n  ⚠ ${sid} 在第 ${i + 1} 轮之前就已经出现复盘页——十句台词没能安全打满十轮，`
        + '需要回去调这一句或它前面那句，别直接用这张不完整的图。');
      break;
    }
    await 打一轮(call, lines[i]);
    process.stdout.write(`第 ${i + 1} 轮 `);
  }
  console.log('');

  if (!打满) return false;

  // 等结局横幅出现（`.endcta`），这时候 20 条消息 + 结局横幅仍在同一个
  // `#thread` 里，还没跳转到复盘页——「完整十轮」要连结局一起拍进去。
  await waitFor(call, `!!document.querySelector('.endcta')`, `${sid} 结局横幅`, 15000);
  await sleep(400);

  const OUT = path.join(DIST, `materials-${sid}-拦下-自留`);
  fs.mkdirSync(OUT, { recursive: true });
  const data = await 存整页(call, 'thread-完整十轮.png');
  const 文件名 = path.join(OUT, 'thread-完整十轮.png');
  fs.writeFileSync(文件名, Buffer.from(data, 'base64'));
  const kb = Math.round(fs.statSync(文件名).size / 1024);
  console.log(`  ✓ ${sid}/thread-完整十轮.png  ${kb} KB`);
  return true;
}

async function main() {
  const chromePath = findChrome();
  if (!chromePath) {
    console.error('找不到 Chrome。装一个，或用 CHROME_PATH 指过去。');
    process.exit(1);
  }

  const server = await startServer();
  const chrome = await launchChrome(chromePath);
  const cdp = await connect(chrome.wsUrl);
  const { call } = await newPage(cdp);

  const 结果 = {};
  try {
    await call('Emulation.setDeviceMetricsOverride', PHONE);
    for (const [sid, lines] of Object.entries(台词表)) {
      console.log(`\n=== ${sid} ===`);
      结果[sid] = await 拍一个场景(call, server.base, sid, lines);
    }
  } finally {
    chrome.kill();
    server.stop();
  }

  console.log('\n汇总：');
  for (const [sid, ok] of Object.entries(结果)) {
    console.log(`  ${sid}  ${ok ? '✓ 完整十轮已存' : '✗ 提前结束，需要调台词，未生成'}`);
  }
  if (Object.values(结果).some((ok) => !ok)) process.exitCode = 1;
}

main();
